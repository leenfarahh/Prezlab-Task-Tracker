from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import activity_log
from app.auth import current_user, require_login
from app.data import fetch_profiles, fetch_task, fetch_task_comments, prefetch
from app.fragments import refreshed_fragments
from app.supabase_client import get_service_client
from app.view_helpers import STATUS_ORDER

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _guard(request: Request, task_id: str) -> HTMLResponse | None:
    """Runs the login check and the task-ownership check together.

    Both need one read from Supabase (the profiles table, and this task's row)
    and neither depends on the other's result, so they go out in a single round
    trip instead of two back to back. Every write path below starts here, and
    on this deployment a round trip is most of what the user waits for.

    Prefetching is gated on there being a session at all - a cookie read, no
    I/O - so an unauthenticated request still does no database work before it
    gets redirected.
    """
    if current_user(request):
        prefetch(fetch_profiles, lambda: fetch_task(task_id))

    redirect = require_login(request)
    if redirect:
        return redirect
    return _task_owner_denial(request, task_id)


def _task_owner_denial(request: Request, task_id: str) -> HTMLResponse | None:
    """Returns a permission-denied response unless the current user is this
    task's creator or assignee, else None. Keeps teammates from editing or
    deleting tasks that aren't theirs to touch.
    """
    user = current_user(request)
    task = fetch_task(task_id)
    if user["id"] in (task["created_by"], task["assignee_id"]):
        return None

    return HTMLResponse(
        templates.get_template("partials/permission_denied_modal.html").render(
            {"request": request, "message": "Only this task's creator or assignee can do that."}
        )
    )


def _archived_parent_denial(request: Request, task_id: str) -> HTMLResponse | None:
    """Returns a denial response if this task's project (or its workstream) is
    still archived, else None.

    Archiving cascades downward - archiving a project or workstream archives
    everything under it - but restoring a single task has no matching cascade
    upward, and letting one through would strand a live task inside an archived
    parent. Those tasks resolve to no project and no workstream, so the board
    filters them out of existence entirely (build_board_context) while the team
    and profile pages render them as "Unknown - Unknown" with a dead link.
    Restore the parent instead; its own cascade brings the tasks back with it.
    """
    service = get_service_client()
    task = fetch_task(task_id)  # already cached by _guard on the write paths
    project = (
        service.table("projects")
        .select("name, workstream_id, is_archived")
        .eq("id", task["project_id"])
        .single()
        .execute()
        .data
    )
    workstream = (
        service.table("workstreams")
        .select("name, is_archived")
        .eq("id", project["workstream_id"])
        .single()
        .execute()
        .data
    )

    if project["is_archived"]:
        message = (
            f"This task's project ({project['name']}) is archived. "
            "Unarchive the project instead - its tasks come back with it."
        )
    elif workstream["is_archived"]:
        message = (
            f"This task's workstream ({workstream['name']}) is archived. "
            "Unarchive the workstream instead - its projects and tasks come back with it."
        )
    else:
        return None

    return HTMLResponse(
        templates.get_template("partials/permission_denied_modal.html").render(
            {"request": request, "message": message}
        )
    )


@router.post("/tasks", response_class=HTMLResponse)
def create_task(
    request: Request,
    project_id: str = Form(...),
    active_workstream: str = Form("all"),
    title: str = Form(...),
    priority: str = Form("medium"),
    assignee_id: str = Form(""),
    due_date: str = Form(""),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

    row = {
        "project_id": project_id,
        "title": title.strip(),
        "priority": priority,
        "assignee_id": assignee_id or None,
        "due_date": due_date or None,
        "created_by": user["id"],
    }
    created = get_service_client().table("tasks").insert(row).execute().data
    # The insert echoes the row back, which is where the id comes from - the
    # event needs it to link through to the task.
    activity_log.log_task_event(
        activity_log.CREATED, {**row, "id": created[0]["id"] if created else None}, user["id"]
    )

    return HTMLResponse(refreshed_fragments(request, active_workstream, project_id))


@router.post("/tasks/{task_id}", response_class=HTMLResponse)
def update_task(
    request: Request,
    task_id: str,
    title: str = Form(...),
    status: str = Form(...),
    priority: str = Form(...),
    assignee_id: str = Form(""),
    due_date: str = Form(""),
    active_workstream: str = Form("all"),
    active_project: str = Form("all"),
):
    denial = _guard(request, task_id)
    if denial:
        return denial
    user = current_user(request)

    # Read before writing. _guard already fetched and memoized this row, so it
    # costs nothing here, and it is the only chance to see what the fields were
    # - once the update lands the cache is dropped and the old values are gone.
    before = dict(fetch_task(task_id))
    changes = {
        "title": title.strip(),
        "status": status,
        "priority": priority,
        "assignee_id": assignee_id or None,
        "due_date": due_date or None,
    }
    get_service_client().table("tasks").update(changes).eq("id", task_id).execute()

    after = {**before, **changes}
    # One save can be a reassignment and a completion at once; each gets its own
    # feed line. The audience spans both assignees so the person who just lost
    # the task and the person who just got it both see it.
    audience = activity_log.audience_for(before, after, actor_id=user["id"])
    for kind, detail in activity_log.diff_task_events(before, after):
        activity_log.log_task_event(kind, after, user["id"], detail=detail, audience=audience)

    return HTMLResponse(refreshed_fragments(request, active_workstream, active_project))


@router.post("/tasks/{task_id}/status", response_class=HTMLResponse)
def set_task_status(
    request: Request,
    task_id: str,
    status: str = Form(...),
    active_workstream: str = Form("all"),
    active_project: str = Form("all"),
):
    """Status-only update, posted by a drag between board columns.

    Deliberately separate from update_task rather than a lighter call into it:
    that one takes the whole task off the edit form and would need every other
    field echoed back just to move a card, which is both a bigger payload and a
    way to clobber a field a teammate changed between the page render and the
    drop. Same permission guard, same oob refresh.
    """
    denial = _guard(request, task_id)
    if denial:
        return denial

    # Nothing else validates status - the edit form is a <select> built from
    # STATUS_ORDER - but this one arrives as a bare form field from client-side
    # JS, so a stale or hand-rolled value would otherwise reach the column
    # check constraint as a 500.
    if status not in STATUS_ORDER:
        return HTMLResponse(
            templates.get_template("partials/permission_denied_modal.html").render(
                {"request": request, "message": f"Unknown status: {status}."}
            )
        )

    user = current_user(request)
    before = dict(fetch_task(task_id))  # memoized by _guard, as in update_task
    get_service_client().table("tasks").update({"status": status}).eq("id", task_id).execute()

    after = {**before, "status": status}
    for kind, detail in activity_log.diff_task_events(before, after):
        activity_log.log_task_event(kind, after, user["id"], detail=detail)

    return HTMLResponse(refreshed_fragments(request, active_workstream, active_project))


def _comments_fragment(request: Request, task_id: str) -> HTMLResponse:
    """Re-renders only the comments block.

    Comments don't change anything the board or sidebar draw, so these routes
    return this instead of refreshed_fragments() - which would also close the
    modal and throw away whatever the user had half-typed in the edit form
    above. The block swaps itself by id and the modal stays put.
    """
    return HTMLResponse(
        templates.get_template("partials/task_comments.html").render(
            {
                "request": request,
                "task_id": task_id,
                "comments": fetch_task_comments(task_id),
                "current_user_id": current_user(request)["id"],
            }
        )
    )


@router.post("/tasks/{task_id}/comments", response_class=HTMLResponse)
def add_task_comment(request: Request, task_id: str, body: str = Form(...)):
    """Post a comment. Open to any logged-in teammate.

    Deliberately require_login and NOT _guard: every other write below is
    creator-or-assignee only, but commenting is how someone raises a question on
    work that isn't theirs, so gating it on ownership would defeat the point.
    """
    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

    # The textarea is `required`, so an empty body means a hand-rolled post
    # rather than a user mistake - drop it and re-render rather than error.
    body = body.strip()
    if body:
        get_service_client().table("task_comments").insert(
            {"task_id": task_id, "author_id": user["id"], "body": body[:4000]}
        ).execute()
        # Also logged as an activity event so the feed is one ordered stream
        # rather than two lists merged by date. The thread in the task modal
        # still reads task_comments - this row carries only a snippet, for the
        # one-line summary the feed shows.
        activity_log.log_task_event(
            activity_log.COMMENTED,
            dict(fetch_task(task_id)),
            user["id"],
            detail={"excerpt": body[:180]},
        )

    return _comments_fragment(request, task_id)


@router.post("/comments/{comment_id}/delete", response_class=HTMLResponse)
def delete_task_comment(request: Request, comment_id: str, task_id: str = Form(...)):
    """Delete your own comment.

    Anyone may comment, but only the author may remove one - a task's owner
    can't delete a teammate's remark. Authorship is enforced as part of the
    delete filter rather than by reading the row first: a delete matching
    nothing is already a no-op, so this is one round trip instead of two with no
    gap between the check and the write.
    """
    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

    get_service_client().table("task_comments").delete().eq("id", comment_id).eq(
        "author_id", user["id"]
    ).execute()

    return _comments_fragment(request, task_id)


@router.get("/partials/confirm-delete-task-modal", response_class=HTMLResponse)
def confirm_delete_task_modal(
    request: Request,
    task_id: str,
    active_workstream: str = "all",
    active_project: str = "all",
):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {
        "request": request,
        "title": "Delete task",
        "message": "Delete this task? This can't be undone.",
        "confirm_url": f"/tasks/{task_id}/delete",
        "confirm_vals": {"active_workstream": active_workstream, "active_project": active_project},
        "confirm_label": "Delete",
    }
    return templates.TemplateResponse("partials/confirm_action_modal.html", ctx)


@router.post("/tasks/{task_id}/archive", response_class=HTMLResponse)
def archive_task(
    request: Request,
    task_id: str,
    active_workstream: str = Form("all"),
    active_project: str = Form("all"),
):
    denial = _guard(request, task_id)
    if denial:
        return denial

    task = dict(fetch_task(task_id))
    get_service_client().table("tasks").update({"is_archived": True}).eq("id", task_id).execute()
    activity_log.log_task_event(activity_log.ARCHIVED, task, current_user(request)["id"])

    return HTMLResponse(refreshed_fragments(request, active_workstream, active_project))


@router.post("/tasks/{task_id}/unarchive", response_class=HTMLResponse)
def unarchive_task(
    request: Request,
    task_id: str,
    active_workstream: str = Form("all"),
    active_project: str = Form("all"),
):
    denial = _guard(request, task_id)
    if denial:
        return denial
    denial = _archived_parent_denial(request, task_id)
    if denial:
        return denial

    task = dict(fetch_task(task_id))
    get_service_client().table("tasks").update({"is_archived": False}).eq("id", task_id).execute()
    activity_log.log_task_event(activity_log.UNARCHIVED, task, current_user(request)["id"])

    return HTMLResponse(refreshed_fragments(request, active_workstream, active_project))


@router.post("/tasks/{task_id}/delete", response_class=HTMLResponse)
def delete_task(
    request: Request,
    task_id: str,
    active_workstream: str = Form("all"),
    active_project: str = Form("all"),
):
    denial = _guard(request, task_id)
    if denial:
        return denial

    # Snapshot before the row is gone: the event outlives the task, and its
    # task_id is set to null by the delete, so the title has to be captured now
    # or the feed line would have nothing to name.
    task = dict(fetch_task(task_id))
    get_service_client().table("tasks").delete().eq("id", task_id).execute()
    activity_log.log_task_event(activity_log.DELETED, task, current_user(request)["id"])

    return HTMLResponse(refreshed_fragments(request, active_workstream, active_project))
