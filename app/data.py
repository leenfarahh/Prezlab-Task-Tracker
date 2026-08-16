from app import digest
from app.request_cache import memo as _memo
from app.request_cache import prefetch
from app.supabase_client import get_service_client
from app.view_helpers import (
    STATUS_COLOR,
    STATUS_LABEL,
    STATUS_ORDER,
    date_buckets,
    format_due_date,
    format_timestamp,
    parse_timestamp,
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


# Workstreams and projects are read whole, once, and split by is_archived here
# rather than by a filter in the query.
#
# Every page that has to name the project behind an old task asks for both the
# live list and the archived one (see build_team_context and friends), and as two
# filtered queries that was two round trips to Supabase - about 700ms of pure
# latency, since these tables hold a few dozen rows and the database itself
# answers either one instantly. One unfiltered read costs a single trip and
# serves both callers from the same memo entry.
#
# Deliberately NOT done for tasks. That table grows without bound while these two
# are capped by how many a team ever creates, and the board - the most-polled
# view in the app - only ever wants the live ones. Making it carry every archived
# task as well would trade a real saving on four pages for a real cost on the
# hottest one. See build_activity_context, which needs both and prefetches them
# side by side instead.


def _all_workstreams() -> list[dict]:
    def load():
        return (
            get_service_client()
            .table("workstreams")
            .select("*")
            .order("created_at")
            .execute()
            .data
        )

    return _memo("workstreams:all", load)


def fetch_workstreams(archived: bool = False) -> list[dict]:
    return [w for w in _all_workstreams() if w["is_archived"] == archived]


def _all_projects() -> list[dict]:
    def load():
        return (
            get_service_client()
            .table("projects")
            .select("*")
            .order("created_at")
            .execute()
            .data
        )

    return _memo("projects:all", load)


def fetch_projects(archived: bool = False, workstream_id: str | None = None) -> list[dict]:
    rows = [p for p in _all_projects() if p["is_archived"] == archived]
    if workstream_id:
        rows = [p for p in rows if p["workstream_id"] == workstream_id]
    return rows


def fetch_task(task_id: str) -> dict:
    def load():
        return get_service_client().table("tasks").select("*").eq("id", task_id).single().execute().data

    return _memo(f"task:{task_id}", load)


def fetch_task_comment_rows(task_id: str) -> list[dict]:
    """The raw comment rows for a task, oldest first.

    Split from fetch_task_comments below so this half - the part that actually
    hits the network - can be handed to prefetch(). The enriching half calls
    fetch_profiles(), and running that inside a prefetch worker would race the
    sibling worker already loading profiles: memo() has no lock, so both would
    miss and issue the same query. Prefetch this one; call the other after.
    """

    def load():
        return (
            get_service_client()
            .table("task_comments")
            .select("*")
            .eq("task_id", task_id)
            .order("created_at")
            .execute()
            .data
        )

    return _memo(f"task_comments:{task_id}", load)


def fetch_task_comments(task_id: str) -> list[dict]:
    """This task's comments, oldest first, each with its author and a display
    timestamp attached.

    Authors are resolved against the memoized profiles map rather than joined,
    so ten comments from three people still cost one read. Enrichment sits
    outside the memo because it is cheap and idempotent, which keeps the cached
    value a plain copy of the row.
    """
    rows = fetch_task_comment_rows(task_id)
    profiles = fetch_profiles()
    for r in rows:
        r["author"] = profiles.get(r["author_id"])
        r["created_display"] = format_timestamp(r["created_at"])
    return rows


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


# ---------- Task activity ----------
# Events are written by app/activity_log.py, each carrying the audience it
# belongs to: the task's creator, its assignee and whoever acted, as of the
# moment it happened. Reading is therefore a filter on that array, not a join -
# see fetch_activity_rows for why that matters.
#
# Everything here is scoped to one user id, which the routes take from the
# session and never from a URL - there is no way to ask for someone else's.

ACTIVITY_LIMIT = 100


def fetch_activity_rows(
    user_id: str,
    scope: str = "",
    actor: str = "",
    kind: str = "",
    project: str = "",
    sort: str = "newest",
) -> list[dict]:
    """Activity rows, raw, capped at ACTIVITY_LIMIT.

    scope="" is everything that has happened; scope="mine" narrows to events
    whose audience includes this person - the task's creator, its assignee and
    whoever acted, as recorded when it happened.

    Everything is the default because the audience is only as good as the data
    behind it. A task with no created_by and no assignee - seeded rows, or
    anything imported - produces an event nobody is the audience for, so it
    existed but appeared in no feed at all. Filtering by audience is offered as
    a narrowing, not imposed as the only view.

    Audience is still what "mine" filters on, rather than joining back to tasks:
    that is what lets it keep showing events whose task has since been deleted,
    and it freezes whose history an event belongs to at the moment it happened,
    so reassigning a task later doesn't move its past out of the previous
    owner's feed.

    The page's filters are applied here, in the query, rather than to the
    returned list. Filtering afterwards would only ever search the most recent
    ACTIVITY_LIMIT events, so asking for "deleted" or for a quiet project would
    come back empty whenever the cap had already been filled by newer, unrelated
    rows - which reads as "nothing happened" rather than "nothing shown".
    """

    def load():
        query = get_service_client().table("task_activity").select("*")
        if scope == "mine":
            query = query.contains("audience", [user_id])
        if actor:
            query = query.eq("actor_id", actor)
        if kind:
            query = query.eq("kind", kind)
        if project:
            query = query.eq("project_id", project)
        return (
            query.order("created_at", desc=(sort != "oldest"))
            .limit(ACTIVITY_LIMIT)
            .execute()
            .data
        )

    return _memo(f"activity_rows:{user_id}:{scope}:{actor}:{kind}:{project}:{sort}", load)


def _is_unread(row: dict, user_id: str, seen_at) -> bool:
    """Someone else's event, newer than this person's read watermark.

    Your own actions never count: archiving your own task shouldn't light up
    your own bell.
    """
    if row.get("actor_id") == user_id:
        return False
    created = parse_timestamp(row.get("created_at"))
    if created is None:
        return False
    return seen_at is None or created > seen_at


def count_unread_activity(user_id: str) -> int:
    """Just the badge number, for the notification bell.

    Counts the same set the page shows by default - everything, minus your own
    actions - so clicking the bell lands on a view where the highlighted rows
    and the number you just saw agree.

    Counted by Postgres rather than in Python. This runs on a 20s poll on every
    page for every signed-in person, and the previous version answered it by
    pulling the most recent hundred activity rows in full - every jsonb detail
    blob and audience array - to then discard all but the number. A count with
    the same two conditions pushed into the query transfers one row instead, and
    is the difference between the badge landing with the page and arriving a
    couple of seconds after it.

    The two conditions mirror _is_unread exactly, and the null check is not
    optional: `actor_id <> $1` is NULL rather than true for an event with no
    recorded actor (backfilled history - see supabase/schema.sql), so a bare neq
    would silently drop precisely the rows that ought to count.
    """
    profile = fetch_profiles().get(user_id) or {}
    seen_at = parse_timestamp(profile.get("comments_seen_at"))

    def load():
        query = (
            get_service_client()
            .table("task_activity")
            .select("id", count="exact")
            .or_(f"actor_id.is.null,actor_id.neq.{user_id}")
        )
        if seen_at:
            query = query.gt("created_at", seen_at.isoformat())
        # limit(1) rather than 0: the count rides in a response header either
        # way, and this asks for one id rather than a whole page of them.
        return query.limit(1).execute().count or 0

    return _memo(f"unread_count:{user_id}", load)


def build_activity_context(
    user_id: str,
    scope: str = "",
    actor: str = "",
    kind: str = "",
    project: str = "",
    sort: str = "newest",
) -> dict:
    """The activity feed: everything that has happened, newest first.

    Comments, creations, assignments, edits, status moves, completions,
    archives and deletions - across tasks, projects and workstreams. The
    person's own actions are included, since the page is a record of the work
    rather than only of what other people did, but they never count as unread.
    Pass scope="mine" to narrow to events involving their own tasks.

    Task and project are resolved live where they still exist, purely so an
    entry can link through to the task modal. Everything the line actually says
    comes from the snapshot on the event, so a deleted task still reads.
    """
    profile = fetch_profiles().get(user_id) or {}
    seen_at = parse_timestamp(profile.get("comments_seen_at"))
    rows = fetch_activity_rows(user_id, scope=scope, actor=actor, kind=kind, project=project, sort=sort)

    profiles = fetch_profiles()
    tasks_by_id = {t["id"]: t for t in fetch_tasks()}
    tasks_by_id.update({t["id"]: t for t in fetch_tasks(archived=True)})
    projects_by_id = {p["id"]: p for p in fetch_projects()}
    projects_by_id.update({p["id"]: p for p in fetch_projects(archived=True)})

    events = []
    for r in rows:
        task = tasks_by_id.get(r.get("task_id"))
        # The event's own project_id first: it still resolves once the task is
        # deleted, where the task-derived lookup has nothing left to go from.
        event_project = projects_by_id.get(r.get("project_id")) or (
            projects_by_id.get(task["project_id"]) if task else None
        )
        detail = r.get("detail") or {}
        events.append(
            {
                **r,
                "actor": profiles.get(r.get("actor_id")),
                "created_display": format_timestamp(r.get("created_at")),
                # None once the task is gone; the template falls back to plain
                # text instead of a link that would open an empty modal.
                "task": task,
                "project": event_project,
                "workstream_id": event_project["workstream_id"] if event_project else "all",
                "entity_type": r.get("entity_type") or "task",
                "title_display": (
                    r.get("entity_title") or r.get("task_title") or (task or {}).get("title") or "a task"
                ),
                "project_display": (event_project or {}).get("name") or r.get("project_name"),
                "assignee": profiles.get(detail.get("to")) if r.get("kind") == "assigned" else None,
                "excerpt": detail.get("excerpt"),
                "detail": detail,
                "is_own": r.get("actor_id") == user_id,
                "is_unread": _is_unread(r, user_id, seen_at),
            }
        )

    return {
        "events": events,
        "unread_count": sum(1 for e in events if e["is_unread"]),
    }


def build_sidebar_context(active_workstream: str, active_project: str = "all") -> dict:
    if active_workstream in ("archived", "archived_projects", "archived_workstreams"):
        # These views aren't filtered by active workstream, so the sidebar's
        # tree doesn't apply - skip the projects and tasks it would need.
        #
        # nav_workstreams is still loaded, and is the one thing these pages do
        # need: it feeds the tab strip that gets you back out (see
        # partials/nav_tabs.html). Without it these were the hardest pages in
        # the app to leave - the tree is empty here, so even opening the sidebar
        # showed no workstream to click.
        return {
            "workstreams": [],
            "nav_workstreams": fetch_workstreams(),
            "active_workstream": active_workstream,
            "active_project": active_project,
        }

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
    # Same list under both keys here: the tree and the tab strip want the same
    # workstreams, and only the archived branch above has to build them apart.
    return {
        "workstreams": workstreams,
        "nav_workstreams": workstreams,
        "active_workstream": active_workstream,
        "active_project": active_project,
    }


def build_board_context(active_workstream: str, active_project: str) -> dict:
    show_archived = active_workstream == "archived"
    # fetch_projects is listed once for either value of show_archived: both are
    # served from the single "projects:all" read (see _all_projects). Passing the
    # archived variant as a second loader would not warm a second entry, it would
    # race the first one onto the network - prefetch runs its loaders in threads
    # and memo() takes no lock, so both would miss the empty cache and issue the
    # same query. The same applies to fetch_workstreams everywhere below.
    prefetch(
        fetch_profiles,
        fetch_workstreams,
        fetch_projects,
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
            # Only meaningful while the board spans the whole workstream. Drilled
            # into one project this counts the workstream's projects, not
            # anything on screen - so it reads as either noise or a wrong number
            # under a heading that already names the single project. None tells
            # the template to drop the tile. The task figures below stay in both
            # scopes because "tasks" is already filtered to whatever is showing.
            "project_count": None if scoped_project else len(tab_projects),
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
        # The create buttons render in dashboard.html's utility strip, which is
        # shared with the team, profile and archived-list pages - none of which
        # call this function. This says "the page below is a live board", so
        # those pages leave the key undefined and the strip stays as it was.
        "show_board_actions": not show_archived,
        "profiles": profiles,
        "flagged_ids": set(),
        "tab_workstreams": workstreams,
        "tab_projects": tab_projects,
        "workstream_overview": workstream_overview,
        "date_summary": None if show_archived else date_buckets(tasks),
    }


def build_archived_projects_context() -> dict:
    prefetch(
        fetch_projects,
        fetch_workstreams,
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
    prefetch(fetch_workstreams, fetch_projects)
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
        fetch_projects,
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
        fetch_projects,
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


def build_my_day_context(user_id: str) -> dict:
    """One person's own tasks, bucketed by when they are due rather than by status.

    Grouped by deadline because this view answers "what do I do today", which
    status columns don't: an in-progress task due in five days and an in-progress
    task that was due last week sit in the same column on the board.

    Scoped to the signed-in user by the caller. Nothing here is a shared view, so
    unlike build_team_context it never resolves other people's assignments.
    """
    prefetch(
        fetch_profiles,
        fetch_projects,
        fetch_tasks,
    )
    # Archived projects included for the same reason build_user_context includes
    # them: a task under an archived project still needs a real name to show.
    projects_by_id = {p["id"]: p for p in fetch_projects() + fetch_projects(archived=True)}

    tasks = [t for t in fetch_tasks() if t["assignee_id"] == user_id]
    for t in tasks:
        t["overdue"] = is_overdue(t)
        t["due_date_display"] = format_due_date(t["due_date"]) if t["due_date"] else None
        project = projects_by_id.get(t["project_id"])
        t["project_name"] = project["name"] if project else "Unknown"
        t["workstream_id"] = project["workstream_id"] if project else None

    buckets = digest.bucket_tasks(tasks)
    return {
        "buckets": buckets,
        "sections": [
            {"key": key, "label": label, "tasks": buckets[key]}
            for key, label in digest.SECTION_LABELS
        ],
        "open_count": sum(len(v) for v in buckets.values()),
    }
