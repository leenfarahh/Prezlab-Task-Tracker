from fastapi import Request
from fastapi.responses import RedirectResponse

from app import tokens
from app.data import fetch_profiles


def current_user(request: Request) -> dict | None:
    """Returns {'id', 'email', 'full_name'} from the session cookie, or None."""
    return request.session.get("user")


def require_login(request: Request) -> RedirectResponse | None:
    """Call at the top of any protected route. Returns a redirect if not logged in, else None.

    Falls back to the refresh_token cookie to re-establish the session when the
    signed session cookie has expired but the underlying token is still valid
    and hasn't been revoked (see app/tokens.py).

    Also re-checks that the session's profile still exists. The session cookie
    is signed client-side state with no server-side expiry tied to the
    profile row, so if that profile is ever deleted (e.g. a database reset)
    the cookie would otherwise keep authenticating requests as a user id
    nothing references anymore - surfacing as a foreign key violation on the
    first write, not as a login failure.
    """
    user = current_user(request)
    if user and _profile_exists(user["id"]):
        return None
    if user:
        request.session.clear()

    raw_token = request.cookies.get(tokens.COOKIE_NAME)
    if raw_token:
        profile = tokens.resolve(raw_token)
        if profile:
            request.session["user"] = profile
            return None

    return RedirectResponse(url="/login", status_code=303)


def _profile_exists(user_id: str) -> bool:
    # Goes through fetch_profiles() rather than querying this one id directly,
    # so it shares the request cache. This check runs at the top of every
    # protected route - including the board and sidebar polls - and as its own
    # query it was an extra Supabase round trip on all of them. Reading the
    # whole (small) profiles table costs the same single round trip and leaves
    # the result cached for the render that follows, which almost always needs
    # profiles anyway.
    return user_id in fetch_profiles()
