import uuid

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import current_user, require_login
from app.data import build_sidebar_context, build_team_context, build_user_context
from app.supabase_client import get_service_client
from app.view_helpers import STATUS_COLOR, STATUS_LABEL, STATUS_ORDER

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _user_ctx(request: Request, user_id: str) -> dict:
    viewer = current_user(request)
    return {
        "request": request,
        "status_order": STATUS_ORDER,
        "status_label": STATUS_LABEL,
        "status_color": STATUS_COLOR,
        "is_self": viewer["id"] == user_id,
        **build_user_context(user_id),
    }


@router.get("/team", response_class=HTMLResponse)
def team_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {
        "request": request,
        "board_template": "partials/team.html",
        **build_sidebar_context("team"),
        **build_team_context(),
    }
    ctx["error"] = None
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/partials/team", response_class=HTMLResponse)
def team_partial(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse("partials/team_list.html", {"request": request, **build_team_context()})


@router.get("/users/{user_id}", response_class=HTMLResponse)
def user_page(request: Request, user_id: str):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {
        "board_template": "partials/user_detail.html",
        **build_sidebar_context("team"),
        **_user_ctx(request, user_id),
    }
    ctx["error"] = None
    return templates.TemplateResponse("dashboard.html", ctx)


@router.post("/users/{user_id}", response_class=HTMLResponse)
def update_user(request: Request, user_id: str, full_name: str = Form(...), role: str = Form("")):
    redirect = require_login(request)
    if redirect:
        return redirect
    viewer = current_user(request)
    if viewer["id"] == user_id:
        get_service_client().table("profiles").update(
            {"full_name": full_name.strip(), "role": role.strip() or None}
        ).eq("id", user_id).execute()

    return templates.TemplateResponse("partials/user_detail.html", _user_ctx(request, user_id))


@router.post("/users/{user_id}/avatar", response_class=HTMLResponse)
async def upload_avatar(request: Request, user_id: str, file: UploadFile):
    redirect = require_login(request)
    if redirect:
        return redirect
    viewer = current_user(request)
    if viewer["id"] == user_id and file.content_type and file.content_type.startswith("image/"):
        ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "jpg"
        path = f"{user_id}/avatar-{uuid.uuid4().hex}.{ext}"
        data = await file.read()
        service = get_service_client()
        service.storage.from_("avatars").upload(
            path, data, file_options={"content-type": file.content_type, "upsert": "true"}
        )
        public_url = service.storage.from_("avatars").get_public_url(path)
        service.table("profiles").update({"avatar_url": public_url}).eq("id", user_id).execute()

    return templates.TemplateResponse("partials/user_detail.html", _user_ctx(request, user_id))
