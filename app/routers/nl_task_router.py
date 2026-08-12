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


def _project_options() -> list[dict]:
    """Non-archived projects annotated with their workstream's name, for the
    Gemini candidate list and the review screen's grouped <select>.
    """
    workstreams_by_id = {w["id"]: w for w in fetch_workstreams()}
    projects = [p for p in fetch_projects() if p["workstream_id"] in workstreams_by_id]
    for p in projects:
        p["workstream_name"] = workstreams_by_id[p["workstream_id"]]["name"]
    return projects


@router.get("/partials/new-task-text-modal", response_class=HTMLResponse)
def new_task_text_modal(request: Request, active_workstream: str = "all", active_project: str = "all"):
    redirect = require_login(request)
    if redirect:
        return redirect
    ctx = {
        "request": request,
        "active_workstream": active_workstream,
        "active_project": active_project,
        "error": None,
    }
    return templates.TemplateResponse("partials/new_task_text_modal.html", ctx)


@router.post("/tasks/parse", response_class=HTMLResponse)
def parse_task_prompt(
    request: Request,
    prompt: str = Form(...),
    active_workstream: str = Form("all"),
    active_project: str = Form("all"),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = current_user(request)

    projects = _project_options()
    profiles = list(fetch_profiles().values())
    project_ids = {p["id"] for p in projects}
    profile_ids = {p["id"] for p in profiles}

    try:
        raw_drafts = extract_tasks(prompt, projects, profiles)
    except GeminiError as exc:
        ctx = {
            "request": request,
            "active_workstream": active_workstream,
            "active_project": active_project,
            "error": str(exc),
            "prompt": prompt,
        }
        return templates.TemplateResponse("partials/new_task_text_modal.html", ctx)

    drafts = []
    for raw in raw_drafts:
        title = str(raw.get("title") or "").strip()[:200]
        if not title:
            continue
        project_id = raw.get("project_id")
        assignee_id = raw.get("assignee_id")
        drafts.append(
            {
                "title": title,
                "description": (str(raw.get("description") or "").strip()[:2000]) or None,
                "priority": raw.get("priority") if raw.get("priority") in ("low", "medium", "high", "urgent") else "medium",
                "due_date": raw.get("due_date") or "",
                "project_id": project_id if project_id in project_ids else "",
                "assignee_id": assignee_id if assignee_id in profile_ids else user["id"],
            }
        )

    ctx = {
        "request": request,
        "active_workstream": active_workstream,
        "active_project": active_project,
        "drafts": drafts,
        "projects": projects,
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

    active_workstream = form.get("active_workstream", "all")
    active_project = form.get("active_project", "all")
    valid_project_ids = {p["id"] for p in fetch_projects()}

    rows = []
    index = 0
    while f"title_{index}" in form:
        if form.get(f"include_{index}") == "on":
            title = form.get(f"title_{index}", "").strip()
            project_id = form.get(f"project_id_{index}", "")
            if title and project_id in valid_project_ids:
                rows.append(
                    {
                        "project_id": project_id,
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

    return HTMLResponse(refreshed_fragments(request, active_workstream, active_project))
