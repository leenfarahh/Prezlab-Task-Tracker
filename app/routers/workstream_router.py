from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_login
from app.data import build_archived_workstreams_context, build_sidebar_context
from app.supabase_client import get_service_client

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _refreshed_archive_fragments(request: Request) -> str:
    """Renders the archived-workstreams list + sidebar as out-of-band swaps, closing the modal."""
    board_ctx = {"request": request, "oob": True, **build_archived_workstreams_context()}
    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context("archived_workstreams")}

    board_html = templates.get_template("partials/archived_workstreams.html").render(board_ctx)
    sidebar_html = templates.get_template("partials/sidebar.html").render(sidebar_ctx)
    return board_html + sidebar_html


@router.get("/partials/archived-workstreams", response_class=HTMLResponse)
def archived_workstreams_partial(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {"request": request, **build_archived_workstreams_context()}
    return templates.TemplateResponse("partials/archived_workstreams.html", ctx)


@router.get("/partials/new-workstream-modal", response_class=HTMLResponse)
def new_workstream_modal(request: Request, project_id: str):
    redirect = require_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        "partials/new_workstream_modal.html",
        {"request": request, "project_id": project_id},
    )


@router.post("/workstreams", response_class=HTMLResponse)
def create_workstream(request: Request, name: str = Form(...), project_id: str = Form(...)):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("workstreams").insert(
        {"name": name.strip(), "project_id": project_id}
    ).execute()

    # oob-only response: closes the modal (empties #modal-root) and refreshes
    # the sidebar in place - same pattern as tasks_router._refreshed_fragments.
    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(project_id)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.get("/partials/edit-workstream-modal", response_class=HTMLResponse)
def edit_workstream_modal(
    request: Request,
    workstream_id: str,
    active_project: str = "all",
    active_workstream: str = "all",
):
    redirect = require_login(request)
    if redirect:
        return redirect
    workstream = (
        get_service_client().table("workstreams").select("*").eq("id", workstream_id).single().execute().data
    )
    return templates.TemplateResponse(
        "partials/edit_workstream_modal.html",
        {
            "request": request,
            "workstream": workstream,
            "active_project": active_project,
            "active_workstream": active_workstream,
        },
    )


@router.post("/workstreams/{workstream_id}", response_class=HTMLResponse)
def update_workstream(
    request: Request,
    workstream_id: str,
    name: str = Form(...),
    active_project: str = Form("all"),
    active_workstream: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("workstreams").update({"name": name.strip()}).eq("id", workstream_id).execute()

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_project, active_workstream)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/workstreams/{workstream_id}/archive", response_class=HTMLResponse)
def archive_workstream(
    request: Request,
    workstream_id: str,
    active_project: str = Form("all"),
    active_workstream: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("workstreams").update({"is_archived": True}).eq("id", workstream_id).execute()
    # A workstream disappearing from the board shouldn't leave its tasks
    # dangling as live-but-orphaned - archive them as one unit.
    get_service_client().table("tasks").update({"is_archived": True}).eq("workstream_id", workstream_id).execute()

    if active_workstream == workstream_id:
        # Its board tab (and filter) is gone now - bounce to the project's
        # "all workstreams" view instead of a now-unreachable workstream filter.
        return HTMLResponse("", headers={"HX-Redirect": f"/?project={active_project}&workstream=all"})

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_project, active_workstream)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/workstreams/{workstream_id}/unarchive", response_class=HTMLResponse)
def unarchive_workstream(request: Request, workstream_id: str):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("workstreams").update({"is_archived": False}).eq("id", workstream_id).execute()
    # Mirrors archive_workstream: tasks that went into archive as part of the
    # workstream come back out as part of it too.
    get_service_client().table("tasks").update({"is_archived": False}).eq("workstream_id", workstream_id).execute()

    return HTMLResponse(_refreshed_archive_fragments(request))
