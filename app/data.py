from app.supabase_client import get_service_client
from app.view_helpers import group_by_status, health_strip_segments, is_overdue


def fetch_profiles() -> dict[str, dict]:
    rows = get_service_client().table("profiles").select("*").execute().data
    return {r["id"]: r for r in rows}


def fetch_workstreams() -> list[dict]:
    return (
        get_service_client()
        .table("workstreams")
        .select("*")
        .eq("is_archived", False)
        .order("created_at")
        .execute()
        .data
    )


def fetch_tasks() -> list[dict]:
    return get_service_client().table("tasks").select("*").order("created_at").execute().data


def build_sidebar_context(active_workstream: str) -> dict:
    workstreams = fetch_workstreams()
    tasks = fetch_tasks()
    for ws in workstreams:
        ws_tasks = [t for t in tasks if t["workstream_id"] == ws["id"]]
        ws["segments"] = health_strip_segments(ws_tasks)
    return {"workstreams": workstreams, "active_workstream": active_workstream}


def build_board_context(active_workstream: str) -> dict:
    workstreams = fetch_workstreams()
    workstreams_by_id = {w["id"]: w for w in workstreams}
    # Drop tasks whose workstream is archived - fetch_workstreams() already
    # excludes those, so without this they'd vanish from the sidebar but keep
    # showing up here, most visibly in the "all" board.
    tasks = [t for t in fetch_tasks() if t["workstream_id"] in workstreams_by_id]

    if active_workstream != "all":
        tasks = [t for t in tasks if t["workstream_id"] == active_workstream]

    for t in tasks:
        t["overdue"] = is_overdue(t)
        owning_workstream = workstreams_by_id[t["workstream_id"]]
        t["workstream_name"] = owning_workstream["name"]
        t["workstream_color"] = owning_workstream["color"]

    if active_workstream == "all":
        title = "All workstreams"
    else:
        match = next((w for w in workstreams if w["id"] == active_workstream), None)
        title = match["name"] if match else "Workstream"

    return {
        "grouped": group_by_status(tasks),
        "board_title": title,
        "active_workstream": active_workstream,
        "profiles": fetch_profiles(),
        "flagged_ids": set(),
    }
