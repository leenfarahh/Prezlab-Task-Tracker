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


def group_by_status(tasks: list[dict]) -> dict[str, list[dict]]:
    grouped = {status: [] for status in STATUS_ORDER}
    for t in tasks:
        grouped.setdefault(t["status"], []).append(t)
    return grouped
