from app.supabase_client import get_service_client
from app.view_helpers import format_due_date, group_by_status, health_strip_segments, initials, is_overdue


def fetch_profiles() -> dict[str, dict]:
    rows = get_service_client().table("profiles").select("*").execute().data
    for r in rows:
        r["initials"] = initials(r["full_name"])
    return {r["id"]: r for r in rows}


def fetch_workstreams(archived: bool = False) -> list[dict]:
    return (
        get_service_client()
        .table("workstreams")
        .select("*")
        .eq("is_archived", archived)
        .order("created_at")
        .execute()
        .data
    )


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


def build_sidebar_context(active_workstream: str) -> dict:
    if active_workstream in ("archived", "archived_workstreams"):
        # The archived views aren't filtered by active workstream, so the
        # picker doesn't apply there - skip fetching data it won't use.
        return {"workstreams": [], "active_workstream": active_workstream}

    workstreams = fetch_workstreams()
    tasks = fetch_tasks()
    for ws in workstreams:
        ws_tasks = [t for t in tasks if t["workstream_id"] == ws["id"]]
        ws["segments"] = health_strip_segments(ws_tasks)
    return {"workstreams": workstreams, "active_workstream": active_workstream}


def build_board_context(active_workstream: str) -> dict:
    show_archived = active_workstream == "archived"
    workstreams = fetch_workstreams()
    workstreams_by_id = {w["id"]: w for w in workstreams}
    # Drop tasks whose workstream is archived - fetch_workstreams() already
    # excludes those, so without this they'd vanish from the sidebar but keep
    # showing up here, most visibly in the "all" board.
    tasks = [t for t in fetch_tasks(archived=show_archived) if t["workstream_id"] in workstreams_by_id]

    if not show_archived and active_workstream != "all":
        tasks = [t for t in tasks if t["workstream_id"] == active_workstream]

    for t in tasks:
        t["overdue"] = is_overdue(t)
        t["due_date_display"] = format_due_date(t["due_date"]) if t["due_date"] else None
        t["workstream_name"] = workstreams_by_id[t["workstream_id"]]["name"]

    if show_archived:
        title = "Archived tasks"
    elif active_workstream == "all":
        title = "All workstreams"
    else:
        match = next((w for w in workstreams if w["id"] == active_workstream), None)
        title = match["name"] if match else "Workstream"

    return {
        "grouped": group_by_status(tasks),
        "board_title": title,
        "active_workstream": active_workstream,
        "show_new_task": not show_archived and active_workstream != "all",
        "profiles": fetch_profiles(),
        "flagged_ids": set(),
        "tab_workstreams": workstreams,
    }


def build_archived_workstreams_context() -> dict:
    workstreams = fetch_workstreams(archived=True)
    archived_tasks = fetch_tasks(archived=True)
    for ws in workstreams:
        ws["archived_task_count"] = sum(1 for t in archived_tasks if t["workstream_id"] == ws["id"])
    return {"workstreams": workstreams}
