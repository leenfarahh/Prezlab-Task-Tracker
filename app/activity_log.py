"""Appends task events to task_activity, for the /activity feed and the bell.

Logging lives in the application rather than in a database trigger because only
the application knows two things the database cannot:

  1. Who acted. Every query goes through one service-role key with no
     per-request database user, so a trigger has no caller identity - the old
     one recorded the task's assignee as the actor, which credited the wrong
     person whenever anyone touched someone else's task.
  2. What the action meant. At the row level an archive, an edit and a status
     change are all the same UPDATE; "moved to done" and "archived" only exist
     as distinct events up here.

Each row carries a snapshot - task title, project name, and the audience it
belongs to - so an event stays readable and correctly attributed even after the
task (or its project) is deleted.
"""

from app.supabase_client import get_service_client

# Kinds the feed knows how to render. Anything else is dropped by the template
# rather than shown raw, so adding one here means adding its phrasing there too.
CREATED = "created"
ASSIGNED = "assigned"
STATUS_CHANGED = "status_changed"
COMPLETED = "completed"
EDITED = "edited"
ARCHIVED = "archived"
UNARCHIVED = "unarchived"
DELETED = "deleted"
COMMENTED = "commented"

# Drives the activity page's "any activity" filter, in the order shown there.
# Insertion order is the display order, so this doubles as the dropdown.
KIND_LABELS = {
    COMMENTED: "Comments",
    CREATED: "Created",
    ASSIGNED: "Reassigned",
    STATUS_CHANGED: "Status changed",
    COMPLETED: "Finished",
    EDITED: "Edited",
    ARCHIVED: "Archived",
    UNARCHIVED: "Unarchived",
    DELETED: "Deleted",
}


# What an event happened to. Tasks were the only kind at first, which is why the
# table is named task_activity and still carries task-shaped columns.
TASK = "task"
PROJECT = "project"
WORKSTREAM = "workstream"


def audience_for_projects(project_ids: list[str], actor_id: str | None = None) -> list[str]:
    """Everyone with a task under these projects, plus the actor.

    Archiving or deleting a project or workstream is only "your activity" if you
    had work inside it - which is exactly the case where you need to be told,
    since your tasks went with it. Creating an empty one therefore reaches only
    its creator, which is correct: there is nobody else it affects yet.
    """
    people: set[str] = {actor_id}
    if project_ids:
        rows = (
            get_service_client()
            .table("tasks")
            .select("created_by, assignee_id")
            .in_("project_id", project_ids)
            .execute()
            .data
        )
        for r in rows:
            people.add(r.get("created_by"))
            people.add(r.get("assignee_id"))
    people.discard(None)
    return sorted(people)


def log_event(
    kind: str,
    entity_type: str,
    entity_id: str | None,
    title: str | None,
    actor_id: str,
    audience: list[str],
    detail: dict | None = None,
    project_id: str | None = None,
) -> None:
    """Write one activity row for a workstream or project.

    Tasks go through log_task_event below, which fills the task-shaped columns
    this table started with; everything reads back through entity_type/
    entity_id/entity_title, which all three kinds share.
    """
    _insert(
        {
            "kind": kind,
            "actor_id": actor_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_title": (title or "")[:200] or None,
            "project_id": project_id,
            "audience": audience,
            "detail": detail or {},
        },
        kind,
    )


def _insert(row: dict, kind: str) -> None:
    """Write the row. Never raises into the caller's request.

    A failed log must not fail the action that produced it: someone deleting a
    workstream should not see an error because the audit row could not be
    written. The action has already committed by the time this runs, so
    swallowing here loses a feed entry rather than leaving anything half-done.
    """
    try:
        get_service_client().table("task_activity").insert(row).execute()
    except Exception:  # noqa: BLE001 - see docstring
        import logging

        logging.getLogger("uvicorn.error").exception("Failed to log activity: %s", kind)


def audience_for(*tasks_or_ids, actor_id: str | None = None) -> list[str]:
    """Everyone an event concerns: each task's creator and assignee, plus the actor.

    Accepts several task dicts so a reassignment can name the outgoing and the
    incoming assignee - the person losing the task and the person gaining it
    both need it in their feed, and the row is written once.
    """
    people: set[str] = set()
    for task in tasks_or_ids:
        if not task:
            continue
        people.add(task.get("created_by"))
        people.add(task.get("assignee_id"))
    people.add(actor_id)
    people.discard(None)
    return sorted(people)


def log_task_event(
    kind: str,
    task: dict,
    actor_id: str,
    detail: dict | None = None,
    project_name: str | None = None,
    audience: list[str] | None = None,
    source_comment_id: str | None = None,
) -> None:
    """Write one activity row for a task. See _insert on failure handling."""
    row = {
        # A deletion is logged after the row is gone, so pointing at it would
        # fail the foreign key on insert - `on delete set null` rewrites rows
        # that already reference the task, it does not permit new ones. The
        # event stands on its snapshot instead, which is what task_title is for.
        "task_id": None if kind == DELETED else task.get("id"),
        "actor_id": actor_id,
        "kind": kind,
        "task_title": (task.get("title") or "")[:200] or None,
        "project_name": project_name,
        # Snapshot, so the page's project filter still matches an event whose
        # task has since been deleted.
        "project_id": task.get("project_id"),
        "audience": audience if audience is not None else audience_for(task, actor_id=actor_id),
        "detail": detail or {},
        # Set for comment events so the schema.sql backfill can tell which
        # comments it has already generated an event for. Without it, a re-run
        # would insert a second event for every comment made since the last one.
        "source_comment_id": source_comment_id,
        # The shared columns every kind of event reads back through. Duplicated
        # from the task_* pair above rather than replacing them, so the rows
        # written before these columns existed still render unchanged.
        "entity_type": TASK,
        "entity_id": None if kind == DELETED else task.get("id"),
        "entity_title": (task.get("title") or "")[:200] or None,
    }
    _insert(row, kind)


def diff_task_events(before: dict, after: dict) -> list[tuple[str, dict]]:
    """Turn one task update into the events it actually represents.

    A single save can be several things at once - reassigned *and* moved to done
    - and each is worth its own line in the feed, so this returns a list rather
    than picking a winner. "Finished" is split out from an ordinary status
    change because reaching done is the event people look for.

    The catch-all EDITED covers the fields with no event of their own (title,
    priority, due date) and is only emitted when one of them actually changed,
    so re-saving a form without touching anything logs nothing.
    """
    events: list[tuple[str, dict]] = []

    if before.get("status") != after.get("status"):
        if after.get("status") == "done":
            events.append((COMPLETED, {"from": before.get("status")}))
        else:
            events.append((STATUS_CHANGED, {"from": before.get("status"), "to": after.get("status")}))

    if before.get("assignee_id") != after.get("assignee_id"):
        events.append((ASSIGNED, {"from": before.get("assignee_id"), "to": after.get("assignee_id")}))

    changed = [
        field
        for field in ("title", "priority", "due_date")
        if before.get(field) != after.get(field)
    ]
    if changed:
        events.append((EDITED, {"fields": changed}))

    return events
