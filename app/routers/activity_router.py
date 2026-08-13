"""Per-person comment activity: the notification bell and the /activity page.

Privacy note, and the reason /activity takes no user id: the feed is built for
whoever is signed in, read from the session cookie. There is no
/users/{id}/activity, so there is no id for someone to swap for a teammate's -
the private-by-default shape comes from the route signature rather than from a
check that a later edit could drop. Nobody can see anyone else's activity.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import current_user, require_login
from app.data import (
    build_activity_context,
    build_sidebar_context,
    count_unread_comments,
    fetch_profiles,
    fetch_projects,
    fetch_tasks,
    prefetch,
)
from app.supabase_client import get_service_client

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _mark_comments_seen(user_id: str) -> None:
    """Push the read watermark to now, clearing the bell."""
    get_service_client().table("profiles").update(
        {"comments_seen_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", user_id).execute()


@router.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request):
    if current_user(request):
        prefetch(fetch_profiles, fetch_tasks, fetch_projects)

    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

    ctx = {
        "request": request,
        "board_template": "partials/activity.html",
        "viewer": fetch_profiles().get(user["id"]),
        **build_sidebar_context("activity"),
        **build_activity_context(user["id"]),
    }
    ctx["error"] = None
    response = templates.TemplateResponse("dashboard.html", ctx)

    # After the response is built, never before. TemplateResponse renders in its
    # constructor, so the HTML above still carries the old watermark and can
    # mark which entries were new; moving this earlier would clear the
    # highlighting on the very page that exists to show it.
    _mark_comments_seen(user["id"])
    return response


@router.get("/partials/notification-bell", response_class=HTMLResponse)
def notification_bell(request: Request):
    """The bell and its unread badge, polled from the top bar on every page."""
    if current_user(request):
        prefetch(fetch_profiles, fetch_tasks)

    # A poll keeps firing after a session expires, and htmx follows redirects -
    # returning require_login's would swap a whole login page into the corner of
    # the toolbar. An empty fragment just leaves the bell blank until the next
    # real navigation redirects properly.
    if require_login(request) is not None:
        return HTMLResponse("")

    user = current_user(request)
    return templates.TemplateResponse(
        "partials/notification_bell.html",
        {"request": request, "unread_count": count_unread_comments(user["id"])},
    )
