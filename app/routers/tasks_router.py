from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import current_user, require_login
from app.fragments import refreshed_fragments
from app.supabase_client import get_service_client

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _task_owner_denial(request: Request, task_id: str) -> HTMLResponse | None:
    """Returns a permission-denied response unless the current user is this
    task's creator or assignee, else None. Keeps teammates from editing or
    deleting tasks that aren't theirs to touch.
    """
    user = current_user(request)
    task = (
        get_service_client()
        .table("tasks")
        .select("created_by, assignee_id")
        .eq("id", task_id)
        .single()
        .execute()
        .data
    )
    if user["id"] in (task["created_by"], task["assignee_id"]):
        return None

    return HTMLResponse(
        templates.get_template("partials/permission_denied_modal.html").render(
            {"request": request, "message": "Only this task's creator or assignee can do that."}
        )
    )


@router.post("/tasks", response_class=HTMLResponse)
def create_task(
    request: Request,
    workstream_id: str = Form(...),
    active_project: str = Form("all"),
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
            "workstream_id": workstream_id,
            "title": title.strip(),
            "priority": priority,
            "assignee_id": assignee_id or None,
            "due_date": due_date or None,
            "created_by": user["id"],
        }
    ).execute()

    return HTMLResponse(refreshed_fragments(request, active_project, workstream_id))


@router.post("/tasks/{task_id}", response_class=HTMLResponse)
def update_task(
    request: Request,
    task_id: str,
    title: str = Form(...),
    status: str = Form(...),
    priority: str = Form(...),
    assignee_id: str = Form(""),
    due_date: str = Form(""),
    active_project: str = Form("all"),
    active_workstream: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    denial = _task_owner_denial(request, task_id)
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

    return HTMLResponse(refreshed_fragments(request, active_project, active_workstream))


@router.get("/partials/confirm-delete-task-modal", response_class=HTMLResponse)
def confirm_delete_task_modal(
    request: Request,
    task_id: str,
    active_project: str = "all",
    active_workstream: str = "all",
):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {
        "request": request,
        "title": "Delete task",
        "message": "Delete this task? This can't be undone.",
        "confirm_url": f"/tasks/{task_id}/delete",
        "confirm_vals": {"active_project": active_project, "active_workstream": active_workstream},
        "confirm_label": "Delete",
    }
    return templates.TemplateResponse("partials/confirm_action_modal.html", ctx)


@router.post("/tasks/{task_id}/archive", response_class=HTMLResponse)
def archive_task(
    request: Request,
    task_id: str,
    active_project: str = Form("all"),
    active_workstream: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    denial = _task_owner_denial(request, task_id)
    if denial:
        return denial

    get_service_client().table("tasks").update({"is_archived": True}).eq("id", task_id).execute()

    return HTMLResponse(refreshed_fragments(request, active_project, active_workstream))


@router.post("/tasks/{task_id}/unarchive", response_class=HTMLResponse)
def unarchive_task(
    request: Request,
    task_id: str,
    active_project: str = Form("all"),
    active_workstream: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    denial = _task_owner_denial(request, task_id)
    if denial:
        return denial

    get_service_client().table("tasks").update({"is_archived": False}).eq("id", task_id).execute()

    return HTMLResponse(refreshed_fragments(request, active_project, active_workstream))


@router.post("/tasks/{task_id}/delete", response_class=HTMLResponse)
def delete_task(
    request: Request,
    task_id: str,
    active_project: str = Form("all"),
    active_workstream: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    denial = _task_owner_denial(request, task_id)
    if denial:
        return denial

    get_service_client().table("tasks").delete().eq("id", task_id).execute()

    return HTMLResponse(refreshed_fragments(request, active_project, active_workstream))
