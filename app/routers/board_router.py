from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import current_user, require_login
from app.data import (
    build_archived_workstreams_context,
    build_archived_projects_context,
    build_board_context,
    build_sidebar_context,
    fetch_profiles,
    fetch_task,
    prefetch,
)
from app.view_helpers import PRIORITY_COLOR, PRIORITY_LABEL, STATUS_COLOR, STATUS_LABEL, STATUS_ORDER

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, workstream: str = "all", project: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect

    ctx = {
        "request": request,
        "board_template": "partials/board.html",
        "status_order": STATUS_ORDER,
        "status_label": STATUS_LABEL,
        "status_color": STATUS_COLOR,
        "priority_label": PRIORITY_LABEL,
        "priority_color": PRIORITY_COLOR,
        "viewer": fetch_profiles().get(current_user(request)["id"]),
        **build_sidebar_context(workstream, project),
        **build_board_context(workstream, project),
    }
    ctx["error"] = None
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/archived", response_class=HTMLResponse)
def archived(request: Request):
    return dashboard(request, workstream="archived", project="all")


@router.get("/archived-projects", response_class=HTMLResponse)
def archived_projects_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    ctx = {
        "request": request,
        "board_template": "partials/archived_projects.html",
        "viewer": fetch_profiles().get(current_user(request)["id"]),
        **build_sidebar_context("archived_projects"),
        **build_archived_projects_context(),
    }
    ctx["error"] = None
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/archived-workstreams", response_class=HTMLResponse)
def archived_workstreams_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    ctx = {
        "request": request,
        "board_template": "partials/archived_workstreams.html",
        "viewer": fetch_profiles().get(current_user(request)["id"]),
        **build_sidebar_context("archived_workstreams"),
        **build_archived_workstreams_context(),
    }
    ctx["error"] = None
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/partials/board", response_class=HTMLResponse)
def board_partial(request: Request, workstream: str = "all", project: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {
        "request": request,
        "status_order": STATUS_ORDER,
        "status_label": STATUS_LABEL,
        "status_color": STATUS_COLOR,
        "priority_label": PRIORITY_LABEL,
        "priority_color": PRIORITY_COLOR,
        **build_board_context(workstream, project),
    }
    return templates.TemplateResponse("partials/board.html", ctx)


@router.get("/partials/sidebar", response_class=HTMLResponse)
def sidebar_partial(request: Request, workstream: str = "all", project: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {"request": request, **build_sidebar_context(workstream, project)}
    return templates.TemplateResponse("partials/sidebar.html", ctx)


@router.get("/partials/empty", response_class=HTMLResponse)
def empty_partial():
    return HTMLResponse("")


@router.get("/partials/new-task-modal", response_class=HTMLResponse)
def new_task_modal(request: Request, project_id: str, active_workstream: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)
    ctx = {
        "request": request,
        "project_id": project_id,
        "active_workstream": active_workstream,
        "profiles": list(fetch_profiles().values()),
        "current_user_id": user["id"],
    }
    return templates.TemplateResponse("partials/new_task_modal.html", ctx)


@router.get("/partials/task-detail-modal", response_class=HTMLResponse)
def task_detail_modal(request: Request, task_id: str, workstream: str = "all", project: str = "all"):
    # Clicking a task card is the most-used interaction in the app, and it used
    # to wait on two round trips back to back: require_login's profile check,
    # then the task row. Neither depends on the other, so they go together.
    # Gated on there being a session at all (a cookie read, no I/O), so an
    # anonymous request still does zero database work before being redirected.
    if current_user(request):
        prefetch(fetch_profiles, lambda: fetch_task(task_id))

    redirect = require_login(request)
    if redirect:
        return redirect
    task = fetch_task(task_id)
    ctx = {
        "request": request,
        "task": task,
        "active_workstream": workstream,
        "active_project": project,
        "status_order": STATUS_ORDER,
        "status_label": STATUS_LABEL,
        "profiles": list(fetch_profiles().values()),
    }
    return templates.TemplateResponse("partials/task_detail_modal.html", ctx)
