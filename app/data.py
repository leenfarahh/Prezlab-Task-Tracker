from app.request_cache import memo as _memo
from app.request_cache import prefetch
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
    def load():
        rows = get_service_client().table("profiles").select("*").execute().data
        for r in rows:
            r["initials"] = initials(r["full_name"])
        return {r["id"]: r for r in rows}

    return _memo("profiles", load)


def fetch_workstreams(archived: bool = False) -> list[dict]:
    def load():
        return (
            get_service_client()
            .table("workstreams")
            .select("*")
            .eq("is_archived", archived)
            .order("created_at")
            .execute()
            .data
        )

    return _memo(f"workstreams:{archived}", load)


def fetch_projects(archived: bool = False, workstream_id: str | None = None) -> list[dict]:
    def load():
        query = get_service_client().table("projects").select("*").eq("is_archived", archived)
        if workstream_id:
            query = query.eq("workstream_id", workstream_id)
        return query.order("created_at").execute().data

    return _memo(f"projects:{archived}:{workstream_id}", load)


def fetch_task(task_id: str) -> dict:
    def load():
        return get_service_client().table("tasks").select("*").eq("id", task_id).single().execute().data

    return _memo(f"task:{task_id}", load)


def fetch_tasks(archived: bool = False) -> list[dict]:
    def load():
        return (
            get_service_client()
            .table("tasks")
            .select("*")
            .eq("is_archived", archived)
            .order("created_at")
            .execute()
            .data
        )

    return _memo(f"tasks:{archived}", load)


def build_sidebar_context(active_workstream: str, active_project: str = "all") -> dict:
    if active_workstream in ("archived", "archived_projects", "archived_workstreams"):
        # These views aren't filtered by active workstream, so the picker
        # doesn't apply there - skip fetching data it won't use.
        return {"workstreams": [], "active_workstream": active_workstream, "active_project": active_project}

    prefetch(fetch_workstreams, fetch_projects, fetch_tasks)
    workstreams = fetch_workstreams()
    projects = fetch_projects()
    tasks = fetch_tasks()
    tasks_by_project: dict[str, list[dict]] = {}
    for t in tasks:
        t["dot_color"] = STATUS_COLOR[t["status"]]
        tasks_by_project.setdefault(t["project_id"], []).append(t)

    for w in workstreams:
        w_projects = [p for p in projects if p["workstream_id"] == w["id"]]
        w_tasks = []
        for p in w_projects:
            p["tasks"] = tasks_by_project.get(p["id"], [])
            w_tasks.extend(p["tasks"])
        w["projects"] = w_projects
        w["segments"] = health_strip_segments(w_tasks)
    return {"workstreams": workstreams, "active_workstream": active_workstream, "active_project": active_project}


def build_board_context(active_workstream: str, active_project: str) -> dict:
    show_archived = active_workstream == "archived"
    prefetch(
        fetch_profiles,
        fetch_workstreams,
        lambda: fetch_projects(archived=show_archived),
        lambda: fetch_tasks(archived=show_archived),
    )
    profiles = fetch_profiles()

    workstreams = fetch_workstreams()
    workstreams_by_id = {w["id"]: w for w in workstreams}

    projects = fetch_projects(archived=show_archived)
    if not show_archived:
        # Drop projects whose workstream is archived - fetch_workstreams() already
        # excludes those, so without this their tasks would vanish from the
        # sidebar but keep showing up here, most visibly on the "all" board.
        projects = [p for p in projects if p["workstream_id"] in workstreams_by_id]
    projects_by_id = {p["id"]: p for p in projects}

    tasks = [t for t in fetch_tasks(archived=show_archived) if t["project_id"] in projects_by_id]

    scoped_workstream = None
    if not show_archived and active_workstream != "all":
        scoped_workstream = workstreams_by_id.get(active_workstream)

    tab_projects = []
    scoped_project = None
    if scoped_workstream:
        tab_projects = [p for p in projects if p["workstream_id"] == scoped_workstream["id"]]
        tab_project_ids = {p["id"] for p in tab_projects}
        tasks = [t for t in tasks if t["project_id"] in tab_project_ids]
        if active_project not in ("all", "by_user"):
            scoped_project = projects_by_id.get(active_project)
            if scoped_project:
                tasks = [t for t in tasks if t["project_id"] == active_project]

    for t in tasks:
        t["overdue"] = is_overdue(t)
        t["due_date_display"] = format_due_date(t["due_date"]) if t["due_date"] else None
        project = projects_by_id.get(t["project_id"])
        t["project_name"] = project["name"] if project else "Unknown"

    by_user = active_project == "by_user" and scoped_workstream is not None

    if show_archived:
        title = "Archived tasks"
    elif not scoped_workstream:
        title = "All workstreams"
    elif by_user:
        title = f"{scoped_workstream['name']} — by user"
    elif scoped_project:
        title = scoped_project["name"]
    else:
        title = scoped_workstream["name"]

    workstream_overview = None
    if scoped_workstream:
        workstream_overview = {
            "project_count": len(tab_projects),
            "total_tasks": len(tasks),
            "done_count": sum(1 for t in tasks if t["status"] == "done"),
            "segments": health_strip_segments(tasks),
        }

    return {
        "grouped": group_by_status(tasks) if not by_user else {},
        "by_user_columns": group_by_assignee(tasks, profiles) if by_user else [],
        "board_title": title,
        "active_workstream": active_workstream,
        "active_project": active_project,
        "scoped_workstream": scoped_workstream,
        "scoped_project": scoped_project,
        "show_new_workstream": not show_archived and not scoped_workstream,
        "show_new_project": bool(scoped_workstream) and active_project == "all",
        "show_new_task": bool(scoped_project),
        "profiles": profiles,
        "flagged_ids": set(),
        "tab_workstreams": workstreams,
        "tab_projects": tab_projects,
        "workstream_overview": workstream_overview,
        "date_summary": None if show_archived else date_buckets(tasks),
    }


def build_archived_projects_context() -> dict:
    prefetch(
        lambda: fetch_projects(archived=True),
        fetch_workstreams,
        lambda: fetch_workstreams(archived=True),
        lambda: fetch_tasks(archived=True),
    )
    projects = fetch_projects(archived=True)
    workstreams = {w["id"]: w for w in fetch_workstreams() + fetch_workstreams(archived=True)}
    archived_tasks = fetch_tasks(archived=True)
    for p in projects:
        p["archived_task_count"] = sum(1 for t in archived_tasks if t["project_id"] == p["id"])
        workstream = workstreams.get(p["workstream_id"])
        p["workstream_name"] = workstream["name"] if workstream else "Unknown"
    return {"projects": projects}


def build_archived_workstreams_context() -> dict:
    prefetch(lambda: fetch_workstreams(archived=True), lambda: fetch_projects(archived=True))
    workstreams = fetch_workstreams(archived=True)
    projects = fetch_projects(archived=True)
    for w in workstreams:
        w["archived_project_count"] = sum(1 for p in projects if p["workstream_id"] == w["id"])
    # Deliberately not called "workstreams": the routes merge this dict on top of
    # build_sidebar_context(), which owns "workstreams" (the live, non-archived
    # tree the sidebar renders). Returning that key here let the archived list
    # win the merge, so the sidebar briefly listed archived workstreams until its
    # own 6s poll replaced them.
    return {"archived_workstreams": workstreams}


def build_team_context() -> dict:
    prefetch(
        fetch_profiles,
        fetch_workstreams,
        lambda: fetch_workstreams(archived=True),
        fetch_projects,
        lambda: fetch_projects(archived=True),
        fetch_tasks,
    )
    profiles = fetch_profiles()
    # Archived rows are included in the name lookups on purpose. A live task
    # should never sit in an archived project (unarchive_task refuses to
    # create one), but rows predating that guard still exist, and naming them
    # "Unknown - Unknown" with a dead link tells nobody anything. Resolving the
    # real name shows where the task actually lives so it can be dealt with.
    projects_by_id = {p["id"]: p for p in fetch_projects() + fetch_projects(archived=True)}
    workstreams_by_id = {w["id"]: w for w in fetch_workstreams() + fetch_workstreams(archived=True)}
    tasks = fetch_tasks()

    for t in tasks:
        t["overdue"] = is_overdue(t)
        t["due_date_display"] = format_due_date(t["due_date"]) if t["due_date"] else None
        project = projects_by_id.get(t["project_id"])
        t["project_name"] = project["name"] if project else "Unknown"
        workstream = workstreams_by_id.get(project["workstream_id"]) if project else None
        t["workstream_id"] = project["workstream_id"] if project else None
        t["workstream_name"] = workstream["name"] if workstream else "Unknown"

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
    prefetch(
        fetch_profiles,
        fetch_workstreams,
        lambda: fetch_workstreams(archived=True),
        fetch_projects,
        lambda: fetch_projects(archived=True),
        fetch_tasks,
    )
    profiles = fetch_profiles()
    person = profiles.get(user_id)

    # Same reasoning as build_team_context: archived rows are here so a legacy
    # orphaned task still resolves to a real project and workstream name.
    projects_by_id = {p["id"]: p for p in fetch_projects() + fetch_projects(archived=True)}
    workstreams_by_id = {w["id"]: w for w in fetch_workstreams() + fetch_workstreams(archived=True)}

    tasks = [t for t in fetch_tasks() if t["assignee_id"] == user_id]
    for t in tasks:
        t["overdue"] = is_overdue(t)
        t["due_date_display"] = format_due_date(t["due_date"]) if t["due_date"] else None
        project = projects_by_id.get(t["project_id"])
        t["project_name"] = project["name"] if project else "Unknown"
        workstream = workstreams_by_id.get(project["workstream_id"]) if project else None
        t["workstream_id"] = project["workstream_id"] if project else None
        t["workstream_name"] = workstream["name"] if workstream else "Unknown"

    return {
        "person": person,
        "grouped": group_by_status(tasks),
        "date_summary": date_buckets(tasks),
        "segments": health_strip_segments(tasks),
        "total_tasks": len(tasks),
        "done_count": sum(1 for t in tasks if t["status"] == "done"),
    }
