from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import current_user, require_login
from app.data import fetch_profiles, fetch_task, prefetch
from app.fragments import refreshed_fragments
from app.supabase_client import get_service_client

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

    get_service_client().table("tasks").insert(
        {
            "project_id": project_id,
            "title": title.strip(),
            "priority": priority,
            "assignee_id": assignee_id or None,
            "due_date": due_date or None,
            "created_by": user["id"],
        }
    ).execute()

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

    get_service_client().table("tasks").update(
        {
            "title": title.strip(),
            "status": status,
            "priority": priority,
            "assignee_id": assignee_id or None,
            "due_date": due_date or None,
        }
    ).eq("id", task_id).execute()

    return HTMLResponse(refreshed_fragments(request, active_workstream, active_project))


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

    get_service_client().table("tasks").update({"is_archived": True}).eq("id", task_id).execute()

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

    get_service_client().table("tasks").update({"is_archived": False}).eq("id", task_id).execute()

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

    get_service_client().table("tasks").delete().eq("id", task_id).execute()

    return HTMLResponse(refreshed_fragments(request, active_workstream, active_project))
