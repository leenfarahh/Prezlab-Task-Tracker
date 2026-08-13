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

from app import activity_log
from app.auth import current_user, require_login
from app.data import (
    build_activity_context,
    build_sidebar_context,
    count_unread_activity,
    fetch_profiles,
    fetch_projects,
    fetch_tasks,
    prefetch,
)
from app.supabase_client import get_service_client
from app.view_helpers import STATUS_LABEL

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _mark_comments_seen(user_id: str) -> None:
    """Push the read watermark to now, clearing the bell."""
    get_service_client().table("profiles").update(
        {"comments_seen_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", user_id).execute()


@router.get("/activity", response_class=HTMLResponse)
def activity_page(
    request: Request,
    scope: str = "",
    actor: str = "",
    kind: str = "",
    project: str = "",
    sort: str = "newest",
):
    if current_user(request):
        prefetch(fetch_profiles, fetch_tasks, fetch_projects)

    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

    # kind and sort go into the query, so they are checked against the values
    # this app actually defines rather than passed through. actor and project
    # are uuids used as equality filters - a bad one matches nothing, which is
    # the correct outcome and needs no separate guard.
    if kind not in activity_log.KIND_LABELS:
        kind = ""
    if sort not in ("newest", "oldest"):
        sort = "newest"
    if scope != "mine":
        scope = ""

    ctx = {
        "request": request,
        "board_template": "partials/activity.html",
        "viewer": fetch_profiles().get(user["id"]),
        # Status moves are logged with the raw enum value, so the feed needs the
        # same labels the board uses to say "In review" rather than "in_review".
        "status_label": STATUS_LABEL,
        # Lets the feed say "reassigned this to you" rather than to your name.
        "current_user_id": user["id"],
        # Filter dropdowns. Built from the whole team and project list rather
        # than from the events on screen, so the options don't shift about as
        # the feed changes - and both are already memoized for this request.
        "kind_labels": activity_log.KIND_LABELS,
        "filter_profiles": sorted(fetch_profiles().values(), key=lambda p: p["full_name"]),
        "filter_projects": sorted(fetch_projects(), key=lambda p: p["name"]),
        "filters": {"scope": scope, "actor": actor, "kind": kind, "project": project, "sort": sort},
        "any_filter": bool(scope or actor or kind or project),
        **build_sidebar_context("activity"),
        **build_activity_context(
            user["id"], scope=scope, actor=actor, kind=kind, project=project, sort=sort
        ),
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
        {"request": request, "unread_count": count_unread_activity(user["id"])},
    )
