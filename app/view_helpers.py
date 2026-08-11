from datetime import date

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
    """Proportional status segments for a workstream's health strip."""
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
    if not task.get("due_date"):
        return False
    return task["due_date"] < date.today().isoformat()


def format_due_date(iso_date: str) -> str:
    """"2026-08-20" -> "Aug 20", or "Aug 20, 2027" once it's no longer this year."""
    d = date.fromisoformat(iso_date)
    suffix = "" if d.year == date.today().year else f", {d.year}"
    return f"{d.strftime('%b')} {d.day}{suffix}"


def initials(full_name: str) -> str:
    parts = full_name.split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def group_by_status(tasks: list[dict]) -> dict[str, list[dict]]:
    grouped = {status: [] for status in STATUS_ORDER}
    for t in tasks:
        grouped.setdefault(t["status"], []).append(t)
    return grouped
