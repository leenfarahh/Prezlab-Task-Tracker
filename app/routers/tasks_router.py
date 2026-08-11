from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import current_user, require_login
from app.data import build_board_context, build_sidebar_context
from app.supabase_client import get_service_client
from app.view_helpers import PRIORITY_COLOR, PRIORITY_LABEL, STATUS_COLOR, STATUS_LABEL, STATUS_ORDER

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


def _refreshed_fragments(request: Request, active_workstream: str) -> str:
    """Renders board + sidebar as out-of-band swaps, closing the modal in the process."""
    board_ctx = {
        "request": request,
        "status_order": STATUS_ORDER,
        "status_label": STATUS_LABEL,
        "status_color": STATUS_COLOR,
        "priority_label": PRIORITY_LABEL,
        "priority_color": PRIORITY_COLOR,
        "oob": True,
        **build_board_context(active_workstream),
    }
    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream)}

    board_html = templates.get_template("partials/board.html").render(board_ctx)
    sidebar_html = templates.get_template("partials/sidebar.html").render(sidebar_ctx)
    return board_html + sidebar_html


@router.post("/tasks", response_class=HTMLResponse)
def create_task(
    request: Request,
    workstream_id: str = Form(...),
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

    return HTMLResponse(_refreshed_fragments(request, workstream_id))


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

    return HTMLResponse(_refreshed_fragments(request, active_workstream))


@router.post("/tasks/{task_id}/archive", response_class=HTMLResponse)
def archive_task(request: Request, task_id: str, active_workstream: str = Form("all")):
    redirect = require_login(request)
    if redirect:
        return redirect
    denial = _task_owner_denial(request, task_id)
    if denial:
        return denial

    get_service_client().table("tasks").update({"is_archived": True}).eq("id", task_id).execute()

    return HTMLResponse(_refreshed_fragments(request, active_workstream))


@router.post("/tasks/{task_id}/unarchive", response_class=HTMLResponse)
def unarchive_task(request: Request, task_id: str, active_workstream: str = Form("all")):
    redirect = require_login(request)
    if redirect:
        return redirect
    denial = _task_owner_denial(request, task_id)
    if denial:
        return denial

    get_service_client().table("tasks").update({"is_archived": False}).eq("id", task_id).execute()

    return HTMLResponse(_refreshed_fragments(request, active_workstream))


@router.post("/tasks/{task_id}/delete", response_class=HTMLResponse)
def delete_task(request: Request, task_id: str, active_workstream: str = Form("all")):
    redirect = require_login(request)
    if redirect:
        return redirect
    denial = _task_owner_denial(request, task_id)
    if denial:
        return denial

    get_service_client().table("tasks").delete().eq("id", task_id).execute()

    return HTMLResponse(_refreshed_fragments(request, active_workstream))
