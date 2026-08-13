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
) -> None:
    """Write one activity row. Never raises into the caller's request.

    A failed log must not fail the action that produced it: someone deleting a
    task should not see an error because the audit row could not be written.
    The action has already committed by the time this runs, so swallowing here
    loses a feed entry rather than leaving anything half-done.
    """
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
        "audience": audience if audience is not None else audience_for(task, actor_id=actor_id),
        "detail": detail or {},
    }
    try:
        get_service_client().table("task_activity").insert(row).execute()
    except Exception:  # noqa: BLE001 - see docstring
        import logging

        logging.getLogger("uvicorn.error").exception("Failed to log task activity: %s", kind)


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
