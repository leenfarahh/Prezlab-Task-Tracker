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
from app.request_cache import memo
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

# Matches the task_priority enum in supabase/schema.sql, lowest number first.
PRIORITY_RANK = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


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
    for key in buckets:
        buckets[key].sort(
            key=lambda t: (PRIORITY_RANK.get(t["priority"], 9), t.get("due_date") or "9999-12-31")
        )
    return buckets


def _timing_weight(task: dict, today: date) -> int:
    if not task.get("due_date"):
        return 3
    days = (date.fromisoformat(task["due_date"]) - today).days
    if days < 0:
        return -1  # overdue
    if days == 0:
        return 0
    if days <= 2:
        return 1
    return 2  # later this week


def rank_tasks(buckets: dict[str, list[dict]], limit: int = 10) -> list[dict]:
    """One flat list across all four sections, ordered by what to do first.

    The sections alone can't answer this, because they rank purely by date: an
    urgent task due Friday lands in the third of them, below every low-priority
    thing that happened to slip. Deadline and priority are both real signals and
    neither one wins outright, so they are scored together:

        score = priority rank * 2 + timing weight

    priority rank runs urgent 0 -> low 3, timing weight runs overdue -1, today 0,
    next two days 1, later this week 2, undated 3. Doubling the priority term is
    what makes it the stronger of the two without letting it override the other:
    an urgent task due Friday (2) outranks a medium (3) or low (5) task that is
    already overdue, but still sits behind anything urgent or high that is
    overdue (-1 and 1). Ties break on the earlier deadline.

    Computed here rather than left to the model. A scoring rule that lives in one
    readable function can be argued with and changed; the same judgement buried
    in a prompt is neither inspectable nor stable between runs.
    """
    today = date.today()
    everything = [t for key, _ in SECTION_LABELS for t in buckets[key]]
    everything.sort(
        key=lambda t: (
            PRIORITY_RANK.get(t["priority"], 9) * 2 + _timing_weight(t, today),
            t.get("due_date") or "9999-12-31",
        )
    )
    return everything[:limit]


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
    """Today's stored digest for this person, or None if it hasn't been written yet.

    Memoized per request so the route can hand it to prefetch() and have it go
    out alongside the task reads instead of waiting behind them. The memo is
    cleared by any write in the same request (see app/supabase_client.py), so a
    refresh still reads back what it just stored.
    """
    day = day or date.today()

    def load():
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

    return memo(f"daily_digest:{user_id}:{day.isoformat()}", load)


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
        ranked=rank_tasks(buckets),
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
    "PRIORITY_RANK",
    "SECTION_LABELS",
    "GeminiError",
    "bucket_tasks",
    "empty_digest",
    "fingerprint",
    "generate",
    "load_cached",
    "rank_tasks",
]
