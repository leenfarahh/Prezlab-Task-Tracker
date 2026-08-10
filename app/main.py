from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import SESSION_SECRET
from app.routers import auth_router, board_router, tasks_router, workstream_router

app = FastAPI(title="Pulse - Prezlab AI Team Tracker")
templates = Jinja2Templates(directory="app/templates")

# Signed session cookie carrying {id, email, full_name} only - see app/supabase_client.py
# for the reasoning on why this app does not carry raw Supabase JWTs around per-request.
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=False, same_site="lax")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth_router.router)
app.include_router(board_router.router)
app.include_router(tasks_router.router)
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
