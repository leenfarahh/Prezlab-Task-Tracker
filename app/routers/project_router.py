from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_login
from app.data import build_archived_projects_context, build_sidebar_context
from app.supabase_client import get_service_client

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _refreshed_archive_fragments(request: Request) -> str:
    """Renders the archived-projects list + sidebar as out-of-band swaps, closing the modal."""
    board_ctx = {"request": request, "oob": True, **build_archived_projects_context()}
    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context("archived_projects")}

    board_html = templates.get_template("partials/archived_projects.html").render(board_ctx)
    sidebar_html = templates.get_template("partials/sidebar.html").render(sidebar_ctx)
    return board_html + sidebar_html


@router.get("/partials/archived-projects", response_class=HTMLResponse)
def archived_projects_partial(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {"request": request, **build_archived_projects_context()}
    return templates.TemplateResponse("partials/archived_projects.html", ctx)


@router.get("/partials/new-project-modal", response_class=HTMLResponse)
def new_project_modal(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse("partials/new_project_modal.html", {"request": request})


@router.post("/projects", response_class=HTMLResponse)
def create_project(request: Request, name: str = Form(...)):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("projects").insert({"name": name.strip()}).execute()

    # oob-only response: closes the modal (empties #modal-root) and refreshes
    # the sidebar in place - same pattern as tasks_router._refreshed_fragments.
    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context("all")}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.get("/partials/edit-project-modal", response_class=HTMLResponse)
def edit_project_modal(request: Request, project_id: str, active_project: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    project = get_service_client().table("projects").select("*").eq("id", project_id).single().execute().data
    return templates.TemplateResponse(
        "partials/edit_project_modal.html",
        {"request": request, "project": project, "active_project": active_project},
    )


@router.get("/partials/confirm-archive-project-modal", response_class=HTMLResponse)
def confirm_archive_project_modal(request: Request, project_id: str, active_project: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {
        "request": request,
        "title": "Archive project",
        "message": "Archive this project? It'll be hidden from the sidebar and board, and its workstreams and tasks will be archived along with it.",
        "confirm_url": f"/projects/{project_id}/archive",
        "confirm_vals": {"active_project": active_project},
        "confirm_label": "Archive",
    }
    return templates.TemplateResponse("partials/confirm_action_modal.html", ctx)


@router.post("/projects/{project_id}", response_class=HTMLResponse)
def update_project(request: Request, project_id: str, name: str = Form(...), active_project: str = Form("all")):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("projects").update({"name": name.strip()}).eq("id", project_id).execute()

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_project)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/projects/{project_id}/archive", response_class=HTMLResponse)
def archive_project(request: Request, project_id: str, active_project: str = Form("all")):
    redirect = require_login(request)
    if redirect:
        return redirect

    service = get_service_client()
    service.table("projects").update({"is_archived": True}).eq("id", project_id).execute()
    # A project disappearing from the board shouldn't leave its workstreams (and
    # their tasks) dangling as live-but-orphaned - archive them as one unit.
    workstream_ids = [w["id"] for w in service.table("workstreams").select("id").eq("project_id", project_id).execute().data]
    service.table("workstreams").update({"is_archived": True}).eq("project_id", project_id).execute()
    if workstream_ids:
        service.table("tasks").update({"is_archived": True}).in_("workstream_id", workstream_ids).execute()

    if active_project == project_id:
        # Its sidebar entry (and filter) is gone now - bounce to "all" instead
        # of leaving the page pointed at an unreachable project filter.
        return HTMLResponse("", headers={"HX-Redirect": "/?project=all"})

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_project)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/projects/{project_id}/unarchive", response_class=HTMLResponse)
def unarchive_project(request: Request, project_id: str):
    redirect = require_login(request)
    if redirect:
        return redirect

    service = get_service_client()
    service.table("projects").update({"is_archived": False}).eq("id", project_id).execute()
    # Mirrors archive_project: workstreams (and their tasks) that went into
    # archive as part of the project come back out as part of it too.
    workstream_ids = [w["id"] for w in service.table("workstreams").select("id").eq("project_id", project_id).execute().data]
    service.table("workstreams").update({"is_archived": False}).eq("project_id", project_id).execute()
    if workstream_ids:
        service.table("tasks").update({"is_archived": False}).in_("workstream_id", workstream_ids).execute()

    return HTMLResponse(_refreshed_archive_fragments(request))
