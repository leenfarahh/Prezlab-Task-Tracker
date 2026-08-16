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
from starlette.background import BackgroundTask

from app import activity_log
from app.auth import current_user, require_login
from app.data import (
    build_activity_context,
    build_sidebar_context,
    count_unread_activity,
    fetch_activity_rows,
    fetch_profiles,
    fetch_projects,
    fetch_tasks,
    fetch_workstreams,
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
    # kind and sort go into the query, so they are checked against the values
    # this app actually defines rather than passed through. actor and project
    # are uuids used as equality filters - a bad one matches nothing, which is
    # the correct outcome and needs no separate guard.
    #
    # Normalised up here, above the prefetch, purely so the prefetch can include
    # the activity query itself - it is keyed on these values, and warming it
    # under an unvalidated key would fill the cache with an entry the render then
    # misses.
    if kind not in activity_log.KIND_LABELS:
        kind = ""
    if sort not in ("newest", "oldest"):
        sort = "newest"
    if scope != "mine":
        scope = ""

    viewer = current_user(request)
    if viewer:
        # Every read this page needs, in one wave. The archived tasks are here
        # rather than left to build_activity_context because an event can name a
        # task that has since been archived, so the page reads both lists either
        # way - and reaching the second one only after this prefetch had finished
        # made it a serial round trip on the slowest page in the app. The
        # activity query joins them for the same reason.
        prefetch(
            fetch_profiles,
            fetch_tasks,
            lambda: fetch_tasks(archived=True),
            fetch_projects,
            fetch_workstreams,
            lambda: fetch_activity_rows(
                viewer["id"], scope=scope, actor=actor, kind=kind, project=project, sort=sort
            ),
        )

    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

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
    #
    # As a background task rather than a plain call, so it runs after the body
    # has gone out instead of in front of it. It is a write nothing on the page
    # depends on - the HTML is already final by this point - and as a blocking
    # call it added a full round trip to Supabase, about 360ms, to how long this
    # page took to appear. Failure here costs a stale bell until the next visit,
    # which is the right thing to trade for the page arriving sooner.
    response.background = BackgroundTask(_mark_comments_seen, user["id"])
    return response


@router.get("/partials/notification-bell", response_class=HTMLResponse)
def notification_bell(request: Request):
    """The bell and its unread badge, polled from the top bar on every page.

    Reads profiles and nothing else. It used to prefetch the whole tasks table
    alongside it, which no part of the count has ever touched - a full table read
    every 20 seconds per open page, for a number derived entirely from
    task_activity and one timestamp on the profile.
    """
    if current_user(request):
        # Not a prefetch: with one loader there is nothing to run it against, and
        # prefetch() no-ops below two anyway.
        fetch_profiles()

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
