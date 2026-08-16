import json
from datetime import date

import httpx

from app.config import GEMINI_API_KEY, GEMINI_MODEL
from app.view_helpers import PRIORITY_LABEL, STATUS_LABEL

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_DRAFT_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "description": {"type": "STRING", "nullable": True},
            "priority": {"type": "STRING", "enum": ["low", "medium", "high", "urgent"]},
            "due_date": {"type": "STRING", "nullable": True, "description": "ISO date YYYY-MM-DD, or null"},
            "project_id": {"type": "STRING", "nullable": True},
            "assignee_id": {"type": "STRING", "nullable": True},
        },
        "required": ["title", "priority"],
    },
}


_DIGEST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "headline": {
            "type": "STRING",
            "description": "One sentence, at most 12 words, leading with whatever is most at risk.",
        },
        "summary": {
            "type": "STRING",
            "description": "Two to four plain sentences on the shape of the day and the week.",
        },
        "focus": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "Up to three short lines, each naming one specific task to act on.",
        },
    },
    "required": ["headline", "summary", "focus"],
}


class GeminiError(Exception):
    """Raised on any non-2xx response or malformed output from the Gemini API."""


def _call_gemini(prompt: str, instructions: str, schema: dict):
    """POSTs one prompt to Gemini and returns the parsed JSON of its reply."""
    if not GEMINI_API_KEY:
        raise GeminiError("GEMINI_API_KEY is not configured.")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": instructions}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }

    try:
        response = httpx.post(
            _ENDPOINT.format(model=GEMINI_MODEL),
            headers={"x-goog-api-key": GEMINI_API_KEY},
            json=body,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except httpx.HTTPError as exc:
        raise GeminiError(f"Gemini API request failed: {exc}") from exc
    except (KeyError, IndexError) as exc:
        raise GeminiError("Gemini API returned an unexpected response shape.") from exc

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GeminiError("Gemini API returned output that wasn't valid JSON.") from exc


def extract_tasks(prompt: str, projects: list[dict], profiles: list[dict]) -> list[dict]:
    """Calls Gemini to turn a free-text prompt into a list of draft tasks.

    projects/profiles are the full candidate lists the model is allowed to
    pick ids from - the caller is responsible for re-validating any returned
    project_id/assignee_id against these before trusting them, since a
    model can return an id that looks plausible but isn't real.
    """
    project_lines = "\n".join(
        f"- {p['id']} | {p['workstream_name']} / {p['name']}" for p in projects
    )
    profile_lines = "\n".join(f"- {p['id']} | {p['full_name']}" for p in profiles)

    instructions = f"""You turn a person's free-text note about work they need to do into one or
more structured task drafts for a project tracker.

Today's date is {date.today().isoformat()}. Resolve relative dates
("tomorrow", "by Friday", "next week") against that.

Split the note into one draft per distinct piece of work. Each draft needs:
- title: short, action-oriented (e.g. "Review Acme deck").
- description: any extra detail from the note, or null if there's none beyond the title.
- priority: low | medium | high | urgent - infer from urgency words, default "medium".
- due_date: an ISO date (YYYY-MM-DD) if a date/deadline was mentioned or implied, else null.
- project_id: pick the single best-matching id from this list based on any project/client/topic
  named in the note, or null if nothing matches:
{project_lines}
- assignee_id: pick the single best-matching id from this list if a specific person was named
  in the note, or null if no one was named:
{profile_lines}

Only ever use ids that appear in the lists above, exactly as written. Never invent an id."""

    drafts = _call_gemini(prompt, instructions, _DRAFT_SCHEMA)

    if not isinstance(drafts, list):
        raise GeminiError("Gemini API returned output that wasn't a list of tasks.")

    return drafts


def _task_line(task: dict) -> str:
    bits = [PRIORITY_LABEL.get(task["priority"], task["priority"])]
    bits.append(STATUS_LABEL.get(task["status"], task["status"]))
    if task.get("due_date"):
        bits.append(f"due {task['due_date']}")
    return f"- {task['title']} ({task.get('project_name', 'Unknown')}) - {', '.join(bits)}"


def _section(heading: str, tasks: list[dict]) -> str:
    if not tasks:
        return f"{heading}: none"
    return heading + ":\n" + "\n".join(_task_line(t) for t in tasks)


def write_daily_digest(
    person_name: str,
    overdue: list[dict],
    due_today: list[dict],
    due_this_week: list[dict],
    no_due_date: list[dict],
) -> dict:
    """Calls Gemini for one person's read on their day, as headline/summary/focus.

    The four lists are already bucketed by the caller so the model never has to
    do date arithmetic - it only has to judge what matters. Everything passed
    in is that one person's open work, since the digest is private to them.
    """
    instructions = f"""You write a short private morning digest for one person about their own
tasks in a work tracker. You are writing to {person_name} directly - use "you", never their name
in the third person.

Today is {date.today().strftime('%A, %d %B %Y')}.

Return:
- headline: one sentence, at most 12 words, leading with whatever is most at risk. If nothing is
  at risk, say that plainly rather than inventing a concern.
- summary: two to four sentences on what the day looks like and what the rest of the week holds.
  Name specific tasks. Mention counts only where a count is the useful fact.
- focus: up to three lines, each naming one specific task worth acting on today, hardest or most
  overdue first. Fewer is fine. Empty if there is genuinely nothing to act on.

Rules:
- Only ever describe tasks from the list you are given. Never invent a task, a deadline, a person,
  or a concern that the data does not support.
- Plain sentences. No greetings, no sign-offs, no motivational filler, no emoji.
- Do not use em dashes."""

    prompt = "\n\n".join(
        [
            _section("Overdue", overdue),
            _section("Due today", due_today),
            _section("Due later this week", due_this_week),
            _section("Open, no due date", no_due_date),
        ]
    )

    digest = _call_gemini(prompt, instructions, _DIGEST_SCHEMA)

    if not isinstance(digest, dict) or not digest.get("summary"):
        raise GeminiError("Gemini API returned output that wasn't a usable digest.")

    focus = digest.get("focus") or []
    return {
        "headline": str(digest.get("headline", "")).strip(),
        "summary": str(digest["summary"]).strip(),
        "focus": [str(f).strip() for f in focus if str(f).strip()][:3],
    }
