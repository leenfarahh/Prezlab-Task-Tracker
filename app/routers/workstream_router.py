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
def new_workstream_modal(request: Request, active_workstream: str = "all", active_project: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {"request": request, "active_workstream": active_workstream, "active_project": active_project}
    return templates.TemplateResponse("partials/new_workstream_modal.html", ctx)


@router.post("/workstreams", response_class=HTMLResponse)
def create_workstream(
    request: Request,
    name: str = Form(...),
    active_workstream: str = Form("all"),
    active_project: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("workstreams").insert({"name": name.strip()}).execute()

    # oob-only response: closes the modal (empties #modal-root) and refreshes
    # the sidebar in place - same pattern as tasks_router._refreshed_fragments.
    # The scope comes from the form rather than being hard-coded to "all"
    # because the sidebar's "+" shortcut can now create a workstream from any
    # view, and rendering the tree as "all" would move the active highlight
    # off whatever the board is still displaying.
    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream, active_project)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.get("/partials/edit-workstream-modal", response_class=HTMLResponse)
def edit_workstream_modal(request: Request, workstream_id: str, active_workstream: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    workstream = get_service_client().table("workstreams").select("*").eq("id", workstream_id).single().execute().data
    return templates.TemplateResponse(
        "partials/edit_workstream_modal.html",
        {"request": request, "workstream": workstream, "active_workstream": active_workstream},
    )


@router.get("/partials/confirm-archive-workstream-modal", response_class=HTMLResponse)
def confirm_archive_workstream_modal(request: Request, workstream_id: str, active_workstream: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {
        "request": request,
        "title": "Archive workstream",
        "message": "Archive this workstream? It'll be hidden from the sidebar and board, and its projects and tasks will be archived along with it.",
        "confirm_url": f"/workstreams/{workstream_id}/archive",
        "confirm_vals": {"active_workstream": active_workstream},
        "confirm_label": "Archive",
    }
    return templates.TemplateResponse("partials/confirm_action_modal.html", ctx)


def _workstream_child_ids(workstream_id: str) -> tuple[list[str], int]:
    """This workstream's project ids and how many tasks sit under them."""
    service = get_service_client()
    project_ids = [
        p["id"]
        for p in service.table("projects").select("id").eq("workstream_id", workstream_id).execute().data
    ]
    if not project_ids:
        return [], 0
    tasks = service.table("tasks").select("id").in_("project_id", project_ids).execute().data
    return project_ids, len(tasks)


@router.get("/partials/confirm-delete-workstream-modal", response_class=HTMLResponse)
def confirm_delete_workstream_modal(
    request: Request,
    workstream_id: str,
    active_workstream: str = "all",
    origin: str = "board",
):
    """Confirmation for a permanent delete - see the note on the project one.

    A workstream is the widest blast radius in the app, so the message names
    both levels underneath it rather than just saying "and its contents".
    """
    redirect = require_login(request)
    if redirect:
        return redirect

    project_ids, task_count = _workstream_child_ids(workstream_id)
    projects = len(project_ids)
    ctx = {
        "request": request,
        "title": "Delete workstream",
        "message": (
            f"Permanently delete this workstream, its {projects} project"
            f"{'' if projects == 1 else 's'} and {task_count} task"
            f"{'' if task_count == 1 else 's'}, including every comment on them? "
            "This can't be undone - archive it instead if there's any chance "
            "you'll want it back."
        ),
        "confirm_url": f"/workstreams/{workstream_id}/delete",
        "confirm_vals": {"active_workstream": active_workstream, "origin": origin},
        "confirm_label": "Delete",
    }
    return templates.TemplateResponse("partials/confirm_action_modal.html", ctx)


@router.post("/workstreams/{workstream_id}/delete", response_class=HTMLResponse)
def delete_workstream(
    request: Request,
    workstream_id: str,
    active_workstream: str = Form("all"),
    origin: str = Form("board"),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    service = get_service_client()
    # Bottom-up, and this order is required rather than tidy:
    # projects.workstream_id is a plain reference with no `on delete cascade`,
    # so deleting the workstream while any project still points at it fails on
    # the foreign key. Tasks then have to go before their projects for the same
    # reason at the level below. Comments and activity cascade off the tasks.
    project_ids, _ = _workstream_child_ids(workstream_id)
    if project_ids:
        service.table("tasks").delete().in_("project_id", project_ids).execute()
    service.table("projects").delete().eq("workstream_id", workstream_id).execute()
    service.table("workstreams").delete().eq("id", workstream_id).execute()

    if origin == "archive":
        return HTMLResponse(_refreshed_archive_fragments(request))

    if active_workstream == workstream_id:
        return HTMLResponse("", headers={"HX-Redirect": "/?workstream=all"})

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/workstreams/{workstream_id}", response_class=HTMLResponse)
def update_workstream(request: Request, workstream_id: str, name: str = Form(...), active_workstream: str = Form("all")):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("workstreams").update({"name": name.strip()}).eq("id", workstream_id).execute()

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/workstreams/{workstream_id}/archive", response_class=HTMLResponse)
def archive_workstream(request: Request, workstream_id: str, active_workstream: str = Form("all")):
    redirect = require_login(request)
    if redirect:
        return redirect

    service = get_service_client()
    service.table("workstreams").update({"is_archived": True}).eq("id", workstream_id).execute()
    # A workstream disappearing from the board shouldn't leave its projects (and
    # their tasks) dangling as live-but-orphaned - archive them as one unit.
    project_ids = [p["id"] for p in service.table("projects").select("id").eq("workstream_id", workstream_id).execute().data]
    service.table("projects").update({"is_archived": True}).eq("workstream_id", workstream_id).execute()
    if project_ids:
        service.table("tasks").update({"is_archived": True}).in_("project_id", project_ids).execute()

    if active_workstream == workstream_id:
        # Its sidebar entry (and filter) is gone now - bounce to "all" instead
        # of leaving the page pointed at an unreachable workstream filter.
        return HTMLResponse("", headers={"HX-Redirect": "/?workstream=all"})

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/workstreams/{workstream_id}/unarchive", response_class=HTMLResponse)
def unarchive_workstream(request: Request, workstream_id: str):
    redirect = require_login(request)
    if redirect:
        return redirect

    service = get_service_client()
    service.table("workstreams").update({"is_archived": False}).eq("id", workstream_id).execute()
    # Mirrors archive_workstream: projects (and their tasks) that went into
    # archive as part of the workstream come back out as part of it too.
    project_ids = [p["id"] for p in service.table("projects").select("id").eq("workstream_id", workstream_id).execute().data]
    service.table("projects").update({"is_archived": False}).eq("workstream_id", workstream_id).execute()
    if project_ids:
        service.table("tasks").update({"is_archived": False}).in_("project_id", project_ids).execute()

    return HTMLResponse(_refreshed_archive_fragments(request))
