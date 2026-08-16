"""My day: a private, generated read on the signed-in person's own workload.

Privacy note, and the reason none of these routes take a user id: the digest is
built for whoever is signed in, read from the session cookie. There is no
/users/{id}/my-day, so there is no id for someone to swap for a teammate's - the
same shape /activity uses, where private-by-default comes from the route
signature rather than from a check a later edit could quietly drop.

Why the Gemini call is on its own route rather than inline in the page: it is
synchronous and can take most of its 30s timeout (app/gemini_client.py), which
would be 30s of blank browser on a page whose task lists were ready instantly.
The page renders its lists immediately, and the digest slot fetches itself once
on load - only when there is nothing cached for today. A cached digest is
rendered inline with no second request at all.

Deliberately not on a poller. Every other self-updating fragment in this app
re-fetches on a timer; doing that here would bill an LLM call every few seconds.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import digest
from app.auth import current_user, require_login
from app.data import (
    build_my_day_context,
    build_sidebar_context,
    fetch_profiles,
    fetch_projects,
    fetch_tasks,
    prefetch,
)
from app.view_helpers import PRIORITY_COLOR, PRIORITY_LABEL, format_timestamp

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _digest_state(user: dict, my_day: dict, *, generate: bool, force: bool = False) -> dict:
    """Resolves what the digest panel should show, in one place for all three routes.

    state is one of:
      ready   - a digest to render, with `stale` saying whether the tasks have
                moved since it was written
      pending - nothing cached yet and we are not the request that generates it,
                so the panel renders a placeholder that fetches itself
      error   - Gemini refused or is unconfigured; the message is shown as-is,
                the same way the "new task from text" modal surfaces its errors
    """
    buckets = my_day["buckets"]

    # An empty plate needs no model. Not cached either: it costs nothing to
    # rebuild, and caching it would mean a person who adds their first task of
    # the day keeps being told they have nothing on.
    empty = digest.empty_digest(buckets)
    if empty:
        return {"state": "ready", "digest": empty, "stale": False, "empty": True, "written": ""}

    cached = None if force else digest.load_cached(user["id"])
    if cached:
        return {
            "state": "ready",
            "digest": cached,
            # The reader's own reason to regenerate: tasks were added, reassigned
            # or moved after this was written, so it no longer describes the day.
            "stale": cached.get("task_fingerprint") != digest.fingerprint(buckets),
            "empty": False,
            # How old this read is. Worth stating on something regenerated once a
            # day: "written 6h ago" is the difference between trusting it and
            # pressing Refresh.
            "written": format_timestamp(cached["created_at"]) if cached.get("created_at") else "",
        }

    if not generate:
        return {"state": "pending", "digest": None, "stale": False, "empty": False, "written": ""}

    profile = fetch_profiles().get(user["id"]) or {}
    try:
        fresh = digest.generate(user["id"], profile.get("full_name") or "there", buckets)
    except digest.GeminiError as exc:
        return {"state": "error", "digest": None, "stale": False, "empty": False, "error": str(exc)}
    return {"state": "ready", "digest": fresh, "stale": False, "empty": False, "written": "just now"}


@router.get("/my-day", response_class=HTMLResponse)
def my_day_page(request: Request):
    if current_user(request):
        prefetch(fetch_profiles, fetch_projects, fetch_tasks)

    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

    my_day = build_my_day_context(user["id"])
    ctx = {
        "request": request,
        "board_template": "partials/my_day.html",
        "viewer": fetch_profiles().get(user["id"]),
        "priority_label": PRIORITY_LABEL,
        "priority_color": PRIORITY_COLOR,
        **build_sidebar_context("my_day"),
        **my_day,
        # generate=False: a page load never blocks on Gemini. If there is nothing
        # cached this comes back "pending" and the panel calls the route below.
        "d": _digest_state(user, my_day, generate=False),
    }
    ctx["error"] = None
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/partials/my-day-digest", response_class=HTMLResponse)
def my_day_digest(request: Request):
    """The digest panel on its own. Generates today's if it doesn't exist yet."""
    if current_user(request):
        prefetch(fetch_profiles, fetch_projects, fetch_tasks)

    # Same reasoning as the notification bell: htmx follows redirects, so
    # returning require_login's would swap a whole login page into the panel.
    if require_login(request) is not None:
        return HTMLResponse("")
    user = current_user(request)

    my_day = build_my_day_context(user["id"])
    ctx = {"request": request, "d": _digest_state(user, my_day, generate=True)}
    return templates.TemplateResponse("partials/my_day_digest.html", ctx)


@router.post("/my-day/refresh", response_class=HTMLResponse)
def refresh_my_day_digest(request: Request):
    """Regenerates today's digest over the top of the cached one, on request.

    A POST because it spends money and replaces stored state - a GET that did
    this would fire on any prefetch or reload.
    """
    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

    my_day = build_my_day_context(user["id"])
    ctx = {"request": request, "d": _digest_state(user, my_day, generate=True, force=True)}
    return templates.TemplateResponse("partials/my_day_digest.html", ctx)
