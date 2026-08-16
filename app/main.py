from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import fragments
from app.asset_version import asset_url
from app.config import SESSION_SECRET
from app.request_cache import RequestCacheMiddleware
from app.routers import (
    activity_router,
    auth_router,
    board_router,
    digest_router,
    nl_task_router,
    project_router,
    tasks_router,
    user_router,
    workstream_router,
)

app = FastAPI(title="Prezlab AI Team Tracker")
templates = Jinja2Templates(directory="app/templates")

# Each module that renders builds its own Jinja2Templates, and Jinja globals are
# per-Environment, so asset_url() has to be registered on every one of them.
# Anything new that renders base.html must be added here, or the template will
# raise "asset_url is undefined" at render time.
for _templates in (
    templates,
    fragments.templates,
    activity_router.templates,
    auth_router.templates,
    board_router.templates,
    digest_router.templates,
    nl_task_router.templates,
    project_router.templates,
    tasks_router.templates,
    user_router.templates,
    workstream_router.templates,
):
    _templates.env.globals["asset_url"] = asset_url

# Signed session cookie carrying {id, email, full_name} only - see app/supabase_client.py
# for the reasoning on why this app does not carry raw Supabase JWTs around per-request.
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=False, same_site="lax")

# Gives each request its own fetch cache, so the sidebar and board contexts
# stop re-reading the same tables from Supabase within a single render.
app.add_middleware(RequestCacheMiddleware)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(activity_router.router)
app.include_router(auth_router.router)
app.include_router(board_router.router)
app.include_router(digest_router.router)
app.include_router(nl_task_router.router)
app.include_router(project_router.router)
app.include_router(tasks_router.router)
app.include_router(user_router.router)
app.include_router(workstream_router.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Catches anything a route didn't handle itself - most commonly a
    # misconfigured or expired Supabase key. Logs the real error server-side
    # but never shows a Python traceback to the person using the app.
    import logging

    logging.getLogger("uvicorn.error").exception("Unhandled exception on %s", request.url.path)
    return templates.TemplateResponse("error.html", {"request": request}, status_code=500)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
