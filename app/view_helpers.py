from datetime import date, datetime, timedelta, timezone

STATUS_ORDER = ["backlog", "in_progress", "at_risk", "blocked", "in_review", "done"]

STATUS_LABEL = {
    "backlog": "Backlog",
    "in_progress": "In progress",
    "at_risk": "At risk",
    "blocked": "Blocked",
    "in_review": "In review",
    "done": "Done",
}

STATUS_COLOR = {
    "backlog": "var(--done)",
    "in_progress": "var(--signal)",
    "at_risk": "var(--at-risk)",
    "blocked": "var(--blocked)",
    "in_review": "var(--in-review)",
    "done": "var(--on-track)",
}

PRIORITY_LABEL = {"low": "Low", "medium": "Med", "high": "High", "urgent": "Urgent"}

PRIORITY_COLOR = {
    "low": "#8b93a1",
    "medium": "var(--signal)",
    "high": "var(--at-risk)",
    "urgent": "var(--blocked)",
}


def health_strip_segments(tasks: list[dict]) -> list[dict]:
    """Proportional status segments for a health strip (a workstream's, or a person's)."""
    if not tasks:
        return []
    total = len(tasks)
    segments = []
    for status in STATUS_ORDER:
        count = sum(1 for t in tasks if t["status"] == status)
        if count:
            segments.append(
                {
                    "status": status,
                    "count": count,
                    "pct": round(count / total * 100, 2),
                    "color": STATUS_COLOR[status],
                }
            )
    return segments


def is_overdue(task: dict) -> bool:
    """A task is overdue only while it is still outstanding.

    A finished task that landed after its due date is history, not a thing to
    chase, so "done" clears the flag regardless of the date.
    """
    if not task.get("due_date") or task.get("status") == "done":
        return False
    return task["due_date"] < date.today().isoformat()


def format_due_date(iso_date: str) -> str:
    """"2026-08-20" -> "Aug 20", or "Aug 20, 2027" once it's no longer this year."""
    d = date.fromisoformat(iso_date)
    suffix = "" if d.year == date.today().year else f", {d.year}"
    return f"{d.strftime('%b')} {d.day}{suffix}"


def parse_timestamp(iso_ts: str) -> datetime | None:
    """Supabase timestamptz string -> aware datetime, or None if unparseable.

    Exists so timestamps are never compared as strings. Postgres trims trailing
    zeros from the fractional seconds, so "…:00+00:00" and "…:00.5+00:00" are
    both valid renderings of times half a second apart, and lexicographic order
    is not reliably chronological across them.

    A "Z" suffix only parses natively from Python 3.11, so it is normalised
    first, and a value that somehow arrives naive is treated as UTC - which is
    what the column stores.
    """
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def format_timestamp(iso_ts: str) -> str:
    """A comment's created_at as "just now" / "12m ago" / "3h ago" / "Aug 13".

    Relative while a thread is live (which is when "5m ago" is the useful fact)
    and absolute once it isn't, falling back to the same "Aug 13" shape
    format_due_date uses so dates read consistently across the app.

    The comparison stays in UTC and only the final display converts to local
    time, since subtracting a naive from an aware datetime would raise.
    """
    dt = parse_timestamp(iso_ts)
    if dt is None:
        return ""

    seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    # Negative when the database clock is a touch ahead of this process, which
    # happens on the row that was just inserted - "just now" is right for it.
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 604800:
        return f"{int(seconds // 86400)}d ago"

    local = dt.astimezone()
    suffix = "" if local.year == date.today().year else f", {local.year}"
    return f"{local.strftime('%b')} {local.day}{suffix}"


def initials(full_name: str) -> str:
    parts = full_name.split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def date_buckets(tasks: list[dict]) -> dict:
    """Counts of tasks with a due date, bucketed as overdue / due this week / upcoming."""
    today = date.today()
    week_end = today + timedelta(days=6)
    buckets = {"overdue": 0, "due_this_week": 0, "upcoming": 0}
    for t in tasks:
        if not t.get("due_date"):
            continue
        d = date.fromisoformat(t["due_date"])
        if d < today:
            # Only counted when still outstanding - see is_overdue.
            if is_overdue(t):
                buckets["overdue"] += 1
        elif d <= week_end:
            buckets["due_this_week"] += 1
        else:
            buckets["upcoming"] += 1
    return buckets


def due_date_sort_key(task: dict) -> tuple[int, str]:
    """Soonest due date first, undated tasks last.

    The leading 0/1 is what parks the undated ones at the bottom - sorting them
    on the date alone would have to treat "no date" as either the far future or
    the distant past, and an undated task is neither the least nor the most
    urgent thing in a column. Dates are ISO strings, so they compare
    chronologically as text with no parsing.
    """
    due = task.get("due_date")
    return (0, due) if due else (1, "")


def group_by_status(tasks: list[dict]) -> dict[str, list[dict]]:
    grouped = {status: [] for status in STATUS_ORDER}
    for t in tasks:
        grouped.setdefault(t["status"], []).append(t)
    # Each column reads top-down as what's due next. Sorting here rather than in
    # the query keeps it independent of how tasks were fetched, and because
    # Python's sort is stable, same-date tasks (and the whole undated group)
    # stay in the created_at order fetch_tasks returns.
    for column in grouped.values():
        column.sort(key=due_date_sort_key)
    return grouped


def group_by_assignee(tasks: list[dict], profiles: dict[str, dict]) -> list[dict]:
    """Tasks bucketed by assignee for the "by user" view - one column per person
    who has at least one task here, sorted by name, with unassigned tasks last.
    """
    by_user: dict[str, list[dict]] = {}
    unassigned = []
    for t in tasks:
        assignee_id = t.get("assignee_id")
        if not assignee_id:
            unassigned.append(t)
            continue
        by_user.setdefault(assignee_id, []).append(t)

    columns = [
        {"assignee": profiles[uid], "tasks": ts}
        for uid, ts in by_user.items()
        if uid in profiles
    ]
    columns.sort(key=lambda c: c["assignee"]["full_name"])
    if unassigned:
        columns.append({"assignee": None, "tasks": unassigned})
    return columns
