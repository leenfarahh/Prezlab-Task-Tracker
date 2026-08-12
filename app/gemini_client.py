import json
from datetime import date

import httpx

from app.config import GEMINI_API_KEY, GEMINI_MODEL

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


class GeminiError(Exception):
    """Raised on any non-2xx response or malformed output from the Gemini API."""


def extract_tasks(prompt: str, projects: list[dict], profiles: list[dict]) -> list[dict]:
    """Calls Gemini to turn a free-text prompt into a list of draft tasks.

    projects/profiles are the full candidate lists the model is allowed to
    pick ids from - the caller is responsible for re-validating any returned
    project_id/assignee_id against these before trusting them, since a
    model can return an id that looks plausible but isn't real.
    """
    if not GEMINI_API_KEY:
        raise GeminiError("GEMINI_API_KEY is not configured.")

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

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": instructions}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _DRAFT_SCHEMA,
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
        drafts = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GeminiError("Gemini API returned output that wasn't valid JSON.") from exc

    if not isinstance(drafts, list):
        raise GeminiError("Gemini API returned output that wasn't a list of tasks.")

    return drafts
