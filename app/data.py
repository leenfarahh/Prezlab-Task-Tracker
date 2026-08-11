from app.supabase_client import get_service_client
from app.view_helpers import (
    STATUS_COLOR,
    STATUS_LABEL,
    STATUS_ORDER,
    date_buckets,
    format_due_date,
    group_by_assignee,
    group_by_status,
    health_strip_segments,
    initials,
    is_overdue,
)


def fetch_profiles() -> dict[str, dict]:
    rows = get_service_client().table("profiles").select("*").execute().data
    for r in rows:
        r["initials"] = initials(r["full_name"])
    return {r["id"]: r for r in rows}


def fetch_projects(archived: bool = False) -> list[dict]:
    return (
        get_service_client()
        .table("projects")
        .select("*")
        .eq("is_archived", archived)
        .order("created_at")
        .execute()
        .data
    )


def fetch_workstreams(archived: bool = False, project_id: str | None = None) -> list[dict]:
    query = get_service_client().table("workstreams").select("*").eq("is_archived", archived)
    if project_id:
        query = query.eq("project_id", project_id)
    return query.order("created_at").execute().data


def fetch_tasks(archived: bool = False) -> list[dict]:
    return (
        get_service_client()
        .table("tasks")
        .select("*")
        .eq("is_archived", archived)
        .order("created_at")
        .execute()
        .data
    )


def build_sidebar_context(active_project: str, active_workstream: str = "all") -> dict:
    if active_project in ("archived", "archived_workstreams", "archived_projects"):
        # These views aren't filtered by active project, so the picker
        # doesn't apply there - skip fetching data it won't use.
        return {"projects": [], "active_project": active_project, "active_workstream": active_workstream}

    projects = fetch_projects()
    workstreams = fetch_workstreams()
    tasks = fetch_tasks()
    tasks_by_workstream: dict[str, list[dict]] = {}
    for t in tasks:
        t["dot_color"] = STATUS_COLOR[t["status"]]
        tasks_by_workstream.setdefault(t["workstream_id"], []).append(t)

    for p in projects:
        p_workstreams = [w for w in workstreams if w["project_id"] == p["id"]]
        p_tasks = []
        for w in p_workstreams:
            w["tasks"] = tasks_by_workstream.get(w["id"], [])
            p_tasks.extend(w["tasks"])
        p["workstreams"] = p_workstreams
        p["segments"] = health_strip_segments(p_tasks)
    return {"projects": projects, "active_project": active_project, "active_workstream": active_workstream}


def build_board_context(active_project: str, active_workstream: str) -> dict:
    show_archived = active_project == "archived"
    profiles = fetch_profiles()

    projects = fetch_projects()
    projects_by_id = {p["id"]: p for p in projects}

    workstreams = fetch_workstreams(archived=show_archived)
    if not show_archived:
        # Drop workstreams whose project is archived - fetch_projects() already
        # excludes those, so without this their tasks would vanish from the
        # sidebar but keep showing up here, most visibly on the "all" board.
        workstreams = [w for w in workstreams if w["project_id"] in projects_by_id]
    workstreams_by_id = {w["id"]: w for w in workstreams}

    tasks = [t for t in fetch_tasks(archived=show_archived) if t["workstream_id"] in workstreams_by_id]

    scoped_project = None
    if not show_archived and active_project != "all":
        scoped_project = projects_by_id.get(active_project)

    tab_workstreams = []
    scoped_workstream = None
    if scoped_project:
        tab_workstreams = [w for w in workstreams if w["project_id"] == scoped_project["id"]]
        tab_workstream_ids = {w["id"] for w in tab_workstreams}
        tasks = [t for t in tasks if t["workstream_id"] in tab_workstream_ids]
        if active_workstream not in ("all", "by_user"):
            scoped_workstream = workstreams_by_id.get(active_workstream)
            if scoped_workstream:
                tasks = [t for t in tasks if t["workstream_id"] == active_workstream]

    for t in tasks:
        t["overdue"] = is_overdue(t)
        t["due_date_display"] = format_due_date(t["due_date"]) if t["due_date"] else None
        ws = workstreams_by_id.get(t["workstream_id"])
        t["workstream_name"] = ws["name"] if ws else "Unknown"

    by_user = active_workstream == "by_user" and scoped_project is not None

    if show_archived:
        title = "Archived tasks"
    elif not scoped_project:
        title = "All projects"
    elif by_user:
        title = f"{scoped_project['name']} — by user"
    elif scoped_workstream:
        title = scoped_workstream["name"]
    else:
        title = scoped_project["name"]

    project_overview = None
    if scoped_project:
        project_overview = {
            "workstream_count": len(tab_workstreams),
            "total_tasks": len(tasks),
            "done_count": sum(1 for t in tasks if t["status"] == "done"),
            "segments": health_strip_segments(tasks),
        }

    return {
        "grouped": group_by_status(tasks) if not by_user else {},
        "by_user_columns": group_by_assignee(tasks, profiles) if by_user else [],
        "board_title": title,
        "active_project": active_project,
        "active_workstream": active_workstream,
        "scoped_project": scoped_project,
        "scoped_workstream": scoped_workstream,
        "show_new_project": not show_archived and not scoped_project,
        "show_new_workstream": bool(scoped_project) and active_workstream == "all",
        "show_new_task": bool(scoped_workstream),
        "profiles": profiles,
        "flagged_ids": set(),
        "tab_projects": projects,
        "tab_workstreams": tab_workstreams,
        "project_overview": project_overview,
        "date_summary": None if show_archived else date_buckets(tasks),
    }


def build_archived_workstreams_context() -> dict:
    workstreams = fetch_workstreams(archived=True)
    projects = {p["id"]: p for p in fetch_projects() + fetch_projects(archived=True)}
    archived_tasks = fetch_tasks(archived=True)
    for ws in workstreams:
        ws["archived_task_count"] = sum(1 for t in archived_tasks if t["workstream_id"] == ws["id"])
        project = projects.get(ws["project_id"])
        ws["project_name"] = project["name"] if project else "Unknown"
    return {"workstreams": workstreams}


def build_archived_projects_context() -> dict:
    projects = fetch_projects(archived=True)
    workstreams = fetch_workstreams(archived=True)
    for p in projects:
        p["archived_workstream_count"] = sum(1 for w in workstreams if w["project_id"] == p["id"])
    return {"projects": projects}


def build_team_context() -> dict:
    profiles = fetch_profiles()
    workstreams_by_id = {w["id"]: w for w in fetch_workstreams()}
    projects_by_id = {p["id"]: p for p in fetch_projects()}
    tasks = fetch_tasks()

    for t in tasks:
        t["overdue"] = is_overdue(t)
        t["due_date_display"] = format_due_date(t["due_date"]) if t["due_date"] else None
        ws = workstreams_by_id.get(t["workstream_id"])
        t["workstream_name"] = ws["name"] if ws else "Unknown"
        project = projects_by_id.get(ws["project_id"]) if ws else None
        t["project_id"] = ws["project_id"] if ws else None
        t["project_name"] = project["name"] if project else "Unknown"

    people = []
    for p in profiles.values():
        their_tasks = [t for t in tasks if t["assignee_id"] == p["id"]]
        people.append(
            {
                **p,
                "total_tasks": len(their_tasks),
                "overdue_count": sum(1 for t in their_tasks if t["overdue"]),
                "done_count": sum(1 for t in their_tasks if t["status"] == "done"),
                "date_summary": date_buckets(their_tasks),
                "grouped": group_by_status(their_tasks),
            }
        )
    people.sort(key=lambda p: p["full_name"])
    return {
        "people": people,
        "status_order": STATUS_ORDER,
        "status_label": STATUS_LABEL,
        "status_color": STATUS_COLOR,
    }


def build_user_context(user_id: str) -> dict:
    profiles = fetch_profiles()
    person = profiles.get(user_id)

    workstreams = fetch_workstreams()
    workstreams_by_id = {w["id"]: w for w in workstreams}
    projects_by_id = {p["id"]: p for p in fetch_projects()}

    tasks = [t for t in fetch_tasks() if t["assignee_id"] == user_id]
    for t in tasks:
        t["overdue"] = is_overdue(t)
        t["due_date_display"] = format_due_date(t["due_date"]) if t["due_date"] else None
        ws = workstreams_by_id.get(t["workstream_id"])
        t["workstream_name"] = ws["name"] if ws else "Unknown"
        project = projects_by_id.get(ws["project_id"]) if ws else None
        t["project_id"] = ws["project_id"] if ws else None
        t["project_name"] = project["name"] if project else "Unknown"

    return {
        "person": person,
        "grouped": group_by_status(tasks),
        "date_summary": date_buckets(tasks),
        "segments": health_strip_segments(tasks),
        "total_tasks": len(tasks),
        "done_count": sum(1 for t in tasks if t["status"] == "done"),
    }
