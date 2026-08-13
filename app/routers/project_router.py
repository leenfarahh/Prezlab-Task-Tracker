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
def new_project_modal(request: Request, workstream_id: str):
    redirect = require_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        "partials/new_project_modal.html",
        {"request": request, "workstream_id": workstream_id},
    )


@router.post("/projects", response_class=HTMLResponse)
def create_project(request: Request, name: str = Form(...), workstream_id: str = Form(...)):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("projects").insert(
        {"name": name.strip(), "workstream_id": workstream_id}
    ).execute()

    # oob-only response: closes the modal (empties #modal-root) and refreshes
    # the sidebar in place - same pattern as tasks_router._refreshed_fragments.
    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(workstream_id)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.get("/partials/edit-project-modal", response_class=HTMLResponse)
def edit_project_modal(
    request: Request,
    project_id: str,
    active_workstream: str = "all",
    active_project: str = "all",
):
    redirect = require_login(request)
    if redirect:
        return redirect
    project = (
        get_service_client().table("projects").select("*").eq("id", project_id).single().execute().data
    )
    return templates.TemplateResponse(
        "partials/edit_project_modal.html",
        {
            "request": request,
            "project": project,
            "active_workstream": active_workstream,
            "active_project": active_project,
        },
    )


@router.get("/partials/confirm-archive-project-modal", response_class=HTMLResponse)
def confirm_archive_project_modal(
    request: Request,
    project_id: str,
    active_workstream: str = "all",
    active_project: str = "all",
):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {
        "request": request,
        "title": "Archive project",
        "message": "Archive this project? It'll be hidden from the sidebar and board, and its tasks will be archived along with it.",
        "confirm_url": f"/projects/{project_id}/archive",
        "confirm_vals": {"active_workstream": active_workstream, "active_project": active_project},
        "confirm_label": "Archive",
    }
    return templates.TemplateResponse("partials/confirm_action_modal.html", ctx)


@router.get("/partials/confirm-delete-project-modal", response_class=HTMLResponse)
def confirm_delete_project_modal(
    request: Request,
    project_id: str,
    active_workstream: str = "all",
    active_project: str = "all",
    origin: str = "board",
):
    """Confirmation for a permanent delete.

    The count is looked up rather than described vaguely: this is one of the two
    actions in the app with no way back, and "and its 14 tasks" is the thing
    that stops someone confirming it out of habit. Tasks are counted in both
    archive states, because the delete takes both.
    """
    redirect = require_login(request)
    if redirect:
        return redirect

    tasks = get_service_client().table("tasks").select("id").eq("project_id", project_id).execute().data
    count = len(tasks)
    ctx = {
        "request": request,
        "title": "Delete project",
        "message": (
            f"Permanently delete this project and its {count} task{'' if count == 1 else 's'}, "
            "including every comment on them? This can't be undone - archive it "
            "instead if there's any chance you'll want it back."
        ),
        "confirm_url": f"/projects/{project_id}/delete",
        "confirm_vals": {
            "active_workstream": active_workstream,
            "active_project": active_project,
            "origin": origin,
        },
        "confirm_label": "Delete",
    }
    return templates.TemplateResponse("partials/confirm_action_modal.html", ctx)


@router.post("/projects/{project_id}/delete", response_class=HTMLResponse)
def delete_project(
    request: Request,
    project_id: str,
    active_workstream: str = Form("all"),
    active_project: str = Form("all"),
    origin: str = Form("board"),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    service = get_service_client()
    # Tasks are removed first and explicitly. tasks.project_id is declared `on
    # delete cascade` so the database would do it anyway, but being explicit
    # keeps this correct regardless of how the FK ended up on a given install,
    # and mirrors how archive_project handles the same parent/child pair.
    # Comments and activity rows hang off the tasks and cascade from them.
    service.table("tasks").delete().eq("project_id", project_id).execute()
    service.table("projects").delete().eq("id", project_id).execute()

    # Deleted from the archived list, which is the page still on screen.
    if origin == "archive":
        return HTMLResponse(_refreshed_archive_fragments(request))

    if active_project == project_id:
        # Same reasoning as archive_project: the board is pointed at a project
        # that no longer exists, so send it somewhere that does.
        return HTMLResponse("", headers={"HX-Redirect": f"/?workstream={active_workstream}&project=all"})

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream, active_project)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/projects/{project_id}", response_class=HTMLResponse)
def update_project(
    request: Request,
    project_id: str,
    name: str = Form(...),
    active_workstream: str = Form("all"),
    active_project: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("projects").update({"name": name.strip()}).eq("id", project_id).execute()

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream, active_project)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/projects/{project_id}/archive", response_class=HTMLResponse)
def archive_project(
    request: Request,
    project_id: str,
    active_workstream: str = Form("all"),
    active_project: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("projects").update({"is_archived": True}).eq("id", project_id).execute()
    # A project disappearing from the board shouldn't leave its tasks
    # dangling as live-but-orphaned - archive them as one unit.
    get_service_client().table("tasks").update({"is_archived": True}).eq("project_id", project_id).execute()

    if active_project == project_id:
        # Its board tab (and filter) is gone now - bounce to the workstream's
        # "all projects" view instead of a now-unreachable project filter.
        return HTMLResponse("", headers={"HX-Redirect": f"/?workstream={active_workstream}&project=all"})

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream, active_project)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/projects/{project_id}/unarchive", response_class=HTMLResponse)
def unarchive_project(request: Request, project_id: str):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("projects").update({"is_archived": False}).eq("id", project_id).execute()
    # Mirrors archive_project: tasks that went into archive as part of the
    # project come back out as part of it too.
    get_service_client().table("tasks").update({"is_archived": False}).eq("project_id", project_id).execute()

    return HTMLResponse(_refreshed_archive_fragments(request))
