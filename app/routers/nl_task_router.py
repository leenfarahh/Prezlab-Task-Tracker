from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import current_user, require_login
from app.data import fetch_profiles, fetch_projects, fetch_workstreams
from app.fragments import refreshed_fragments
from app.gemini_client import GeminiError, extract_tasks
from app.supabase_client import get_service_client

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _workstream_options() -> list[dict]:
    """Non-archived workstreams annotated with their project's name, for the
    Gemini candidate list and the review screen's grouped <select>.
    """
    projects_by_id = {p["id"]: p for p in fetch_projects()}
    workstreams = [w for w in fetch_workstreams() if w["project_id"] in projects_by_id]
    for w in workstreams:
        w["project_name"] = projects_by_id[w["project_id"]]["name"]
    return workstreams


@router.get("/partials/new-task-text-modal", response_class=HTMLResponse)
def new_task_text_modal(request: Request, active_project: str = "all", active_workstream: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {
        "request": request,
        "active_project": active_project,
        "active_workstream": active_workstream,
        "error": None,
    }
    return templates.TemplateResponse("partials/new_task_text_modal.html", ctx)


@router.post("/tasks/parse", response_class=HTMLResponse)
def parse_task_prompt(
    request: Request,
    prompt: str = Form(...),
    active_project: str = Form("all"),
    active_workstream: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

    workstreams = _workstream_options()
    profiles = list(fetch_profiles().values())
    workstream_ids = {w["id"] for w in workstreams}
    profile_ids = {p["id"] for p in profiles}

    try:
        raw_drafts = extract_tasks(prompt, workstreams, profiles)
    except GeminiError as exc:
        ctx = {
            "request": request,
            "active_project": active_project,
            "active_workstream": active_workstream,
            "error": str(exc),
            "prompt": prompt,
        }
        return templates.TemplateResponse("partials/new_task_text_modal.html", ctx)

    drafts = []
    for raw in raw_drafts:
        title = str(raw.get("title") or "").strip()[:200]
        if not title:
            continue
        workstream_id = raw.get("workstream_id")
        assignee_id = raw.get("assignee_id")
        drafts.append(
            {
                "title": title,
                "description": (str(raw.get("description") or "").strip()[:2000]) or None,
                "priority": raw.get("priority") if raw.get("priority") in ("low", "medium", "high", "urgent") else "medium",
                "due_date": raw.get("due_date") or "",
                "workstream_id": workstream_id if workstream_id in workstream_ids else "",
                "assignee_id": assignee_id if assignee_id in profile_ids else user["id"],
            }
        )

    ctx = {
        "request": request,
        "active_project": active_project,
        "active_workstream": active_workstream,
        "drafts": drafts,
        "workstreams": workstreams,
        "profiles": profiles,
        "current_user_id": user["id"],
    }
    return templates.TemplateResponse("partials/task_drafts_review_modal.html", ctx)


@router.post("/tasks/batch", response_class=HTMLResponse)
async def create_tasks_batch(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)
    form = await request.form()

    active_project = form.get("active_project", "all")
    active_workstream = form.get("active_workstream", "all")
    valid_workstream_ids = {w["id"] for w in fetch_workstreams()}

    rows = []
    index = 0
    while f"title_{index}" in form:
        if form.get(f"include_{index}") == "on":
            title = form.get(f"title_{index}", "").strip()
            workstream_id = form.get(f"workstream_id_{index}", "")
            if title and workstream_id in valid_workstream_ids:
                rows.append(
                    {
                        "workstream_id": workstream_id,
                        "title": title,
                        "description": form.get(f"description_{index}", "").strip() or None,
                        "priority": form.get(f"priority_{index}", "medium"),
                        "assignee_id": form.get(f"assignee_id_{index}") or None,
                        "due_date": form.get(f"due_date_{index}") or None,
                        "created_by": user["id"],
                    }
                )
        index += 1

    if rows:
        get_service_client().table("tasks").insert(rows).execute()

    return HTMLResponse(refreshed_fragments(request, active_project, active_workstream))
