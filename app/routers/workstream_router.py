from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import current_user, require_login
from app.data import build_sidebar_context
from app.supabase_client import get_service_client

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

COLOR_CHOICES = [
    ("#4C5FD5", "Indigo"),
    ("#3F9169", "Green"),
    ("#E8A33D", "Amber"),
    ("#8E6FD1", "Purple"),
    ("#D64545", "Red"),
]


@router.get("/partials/new-workstream-modal", response_class=HTMLResponse)
def new_workstream_modal(request: Request, active_workstream: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        "partials/new_workstream_modal.html",
        {"request": request, "colors": COLOR_CHOICES, "active_workstream": active_workstream},
    )


@router.post("/workstreams", response_class=HTMLResponse)
def create_workstream(
    request: Request,
    name: str = Form(...),
    client_label: str = Form(""),
    color: str = Form(COLOR_CHOICES[0][0]),
    active_workstream: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

    get_service_client().table("workstreams").insert(
        {
            "name": name.strip(),
            "client_label": client_label.strip() or None,
            "color": color,
            "owner_id": user["id"],
        }
    ).execute()

    # oob-only response: closes the modal (empties #modal-root) and refreshes
    # the sidebar in place - same pattern as tasks_router._refreshed_fragments.
    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.get("/partials/edit-workstream-modal", response_class=HTMLResponse)
def edit_workstream_modal(request: Request, workstream_id: str, active_workstream: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    workstream = (
        get_service_client().table("workstreams").select("*").eq("id", workstream_id).single().execute().data
    )
    return templates.TemplateResponse(
        "partials/edit_workstream_modal.html",
        {"request": request, "workstream": workstream, "colors": COLOR_CHOICES, "active_workstream": active_workstream},
    )


@router.post("/workstreams/{workstream_id}", response_class=HTMLResponse)
def update_workstream(
    request: Request,
    workstream_id: str,
    name: str = Form(...),
    client_label: str = Form(""),
    color: str = Form(COLOR_CHOICES[0][0]),
    active_workstream: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("workstreams").update(
        {
            "name": name.strip(),
            "client_label": client_label.strip() or None,
            "color": color,
        }
    ).eq("id", workstream_id).execute()

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))


@router.post("/workstreams/{workstream_id}/archive", response_class=HTMLResponse)
def archive_workstream(request: Request, workstream_id: str, active_workstream: str = Form("all")):
    redirect = require_login(request)
    if redirect:
        return redirect

    get_service_client().table("workstreams").update({"is_archived": True}).eq("id", workstream_id).execute()

    if active_workstream == workstream_id:
        # Its sidebar entry (and filter) is gone now - bounce to "all" instead
        # of leaving the page pointed at an unreachable workstream filter.
        return HTMLResponse("", headers={"HX-Redirect": "/?workstream=all"})

    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream)}
    return HTMLResponse(templates.get_template("partials/sidebar.html").render(sidebar_ctx))
