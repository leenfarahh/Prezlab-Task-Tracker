from fastapi import Request
from fastapi.responses import RedirectResponse

from app import tokens


def current_user(request: Request) -> dict | None:
    """Returns {'id', 'email', 'full_name'} from the session cookie, or None."""
    return request.session.get("user")


def require_login(request: Request) -> RedirectResponse | None:
    """Call at the top of any protected route. Returns a redirect if not logged in, else None.

    Falls back to the refresh_token cookie to re-establish the session when the
    signed session cookie has expired but the underlying token is still valid
    and hasn't been revoked (see app/tokens.py).
    """
    if current_user(request):
        return None

    raw_token = request.cookies.get(tokens.COOKIE_NAME)
    if raw_token:
        profile = tokens.resolve(raw_token)
        if profile:
            request.session["user"] = profile
            return None

    return RedirectResponse(url="/login", status_code=303)
