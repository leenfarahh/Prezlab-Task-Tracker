from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import tokens
from app.config import ALLOWED_LOGIN_EMAILS
from app.supabase_client import get_service_client

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# No Supabase Auth: email is the identity, gated to an explicit allow-list
# (ALLOWED_LOGIN_EMAILS in .env) rather than a company domain. There is no
# proof the requester owns the address - acceptable only because this app is
# reachable solely on the enterprise network, not the public internet.


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login")
def login(request: Request, email: str = Form(...)):
    email = email.strip().lower()
    if email not in ALLOWED_LOGIN_EMAILS:
        return RedirectResponse(
            url=f"/login?error={quote('This email is not on the access list. Contact an admin to be added.')}",
            status_code=303,
        )

    service = get_service_client()
    existing = service.table("profiles").select("*").eq("email", email).execute().data
    if existing:
        profile = existing[0]
    else:
        profile = (
            service.table("profiles")
            .insert({"full_name": email.split("@")[0], "email": email})
            .execute()
            .data[0]
        )

    request.session["user"] = {
        "id": profile["id"],
        "email": profile["email"],
        "full_name": profile["full_name"],
    }

    response = RedirectResponse(url="/", status_code=303)
    tokens.issue(response, profile["id"])
    return response


@router.post("/logout")
def logout(request: Request):
    raw_token = request.cookies.get(tokens.COOKIE_NAME)
    if raw_token:
        tokens.revoke(raw_token)

    request.session.clear()
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(tokens.COOKIE_NAME)
    return response
