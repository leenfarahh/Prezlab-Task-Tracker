"""Per-person daily digest: what today and the rest of the week look like.

Private to its subject the same way the activity feed is (see the note at the
top of app/routers/activity_router.py): every function here takes the user id
the route read from the session cookie, and no route accepts one from a URL.
There is no /users/{id}/my-day to swap an id into.

Generated lazily on the first view of the day rather than on a schedule. This
app has no worker process to run a scheduler in, and a digest nobody opens is a
Gemini call nobody needed - so the trigger is the same one profiles.comments_seen_at
uses: a person showing up. The row is cached in Postgres (see the daily_digests
section of supabase/schema.sql), so a restart or a second worker doesn't buy a
second generation.
"""

import hashlib
from datetime import date, timedelta

from app.gemini_client import GeminiError, write_daily_digest
from app.supabase_client import get_service_client

# The section order the page renders in and the model reads in: most pressing
# first. Named here so the template, the fingerprint and the prompt can't drift
# apart from one another.
SECTION_LABELS = [
    ("overdue", "Overdue"),
    ("due_today", "Today"),
    ("due_this_week", "Rest of this week"),
    ("no_due_date", "No due date"),
]


def bucket_tasks(tasks: list[dict]) -> dict[str, list[dict]]:
    """Splits one person's tasks into the four sections the digest is built from.

    Done tasks are dropped outright. A finished task is not work for today, and
    including them would have the model reporting on things already behind the
    reader (this is the same rule is_overdue applies - see app/view_helpers.py).

    Anything due beyond this week is also left out. The page is a day-and-week
    view, and a task due in three weeks is neither.
    """
    today = date.today()
    week_end = today + timedelta(days=6)
    buckets: dict[str, list[dict]] = {key: [] for key, _ in SECTION_LABELS}

    for t in tasks:
        if t["status"] == "done":
            continue
        if not t.get("due_date"):
            buckets["no_due_date"].append(t)
            continue
        due = date.fromisoformat(t["due_date"])
        if due < today:
            buckets["overdue"].append(t)
        elif due == today:
            buckets["due_today"].append(t)
        elif due <= week_end:
            buckets["due_this_week"].append(t)

    # Within a section, the most urgent thing first, then the oldest deadline.
    priority_rank = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    for key in buckets:
        buckets[key].sort(
            key=lambda t: (priority_rank.get(t["priority"], 9), t.get("due_date") or "9999-12-31")
        )
    return buckets


def fingerprint(buckets: dict[str, list[dict]]) -> str:
    """A short hash of exactly what the model was shown.

    Compared against the stored one to tell whether a cached digest still
    describes the board. Covers the fields that would change what it says - a
    new task, a reassignment away, a moved deadline, a status change - and not
    fields that wouldn't, so an unrelated edit doesn't nag the reader to
    regenerate something still accurate.
    """
    parts = [
        f"{key}|{t['id']}|{t['status']}|{t['priority']}|{t.get('due_date')}|{t['title']}"
        for key, _ in SECTION_LABELS
        for t in buckets[key]
    ]
    return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()[:32]


def load_cached(user_id: str, day: date | None = None) -> dict | None:
    """Today's stored digest for this person, or None if it hasn't been written yet."""
    day = day or date.today()
    rows = (
        get_service_client()
        .table("daily_digests")
        .select("*")
        .eq("user_id", user_id)
        .eq("digest_date", day.isoformat())
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def _store(user_id: str, day: date, digest: dict, task_fingerprint: str) -> dict:
    row = {
        "user_id": user_id,
        "digest_date": day.isoformat(),
        "headline": digest["headline"],
        "summary": digest["summary"],
        "focus": digest["focus"],
        "task_fingerprint": task_fingerprint,
    }
    # Upsert on the (user_id, digest_date) unique constraint: a refresh replaces
    # the day's row rather than stacking a second one behind it.
    (
        get_service_client()
        .table("daily_digests")
        .upsert(row, on_conflict="user_id,digest_date")
        .execute()
    )
    return row


def generate(user_id: str, full_name: str, buckets: dict[str, list[dict]]) -> dict:
    """Calls Gemini for a fresh digest and caches it as today's. Raises GeminiError."""
    digest = write_daily_digest(
        full_name,
        overdue=buckets["overdue"],
        due_today=buckets["due_today"],
        due_this_week=buckets["due_this_week"],
        no_due_date=buckets["no_due_date"],
    )
    return _store(user_id, date.today(), digest, fingerprint(buckets))


def empty_digest(buckets: dict[str, list[dict]]) -> dict | None:
    """The digest for a person with nothing open, written here rather than by Gemini.

    Spending an API call and several seconds of the reader's time to be told
    there is nothing to read would be worse than the fixed sentence, and there
    is nothing for the model to add.
    """
    if any(buckets[key] for key, _ in SECTION_LABELS):
        return None
    return {
        "headline": "Nothing on your plate today.",
        "summary": "You have no open tasks due today or this week, and nothing overdue.",
        "focus": [],
        "task_fingerprint": fingerprint(buckets),
    }


__all__ = [
    "SECTION_LABELS",
    "GeminiError",
    "bucket_tasks",
    "empty_digest",
    "fingerprint",
    "generate",
    "load_cached",
]
