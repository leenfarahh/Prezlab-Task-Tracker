"""Creator checks for workstreams and projects.

Tasks have their own rule in app/routers/tasks_router.py (creator OR assignee,
since an assignee has to be able to work on what they were handed). Workstreams
and projects are creator-only: nobody is "assigned" one, and the actions being
guarded - rename, archive, unarchive, delete - are all things the person who
made it should decide about.

Enforcement lives here rather than only in the templates. Hiding a button stops
the accident; it does not stop a hand-rolled POST, and delete cascades to every
task and comment underneath.
"""

from fastapi import Request
from fastapi.responses import HTMLResponse

from app.auth import current_user
from app.fragments import templates
from app.supabase_client import get_service_client

# Rows predating created_by. They have no recorded creator, so there is nobody
# to enforce on behalf of - see the note in schema.sql.
UNOWNED = None


def creator_denial(request: Request, table: str, row_id: str, noun: str) -> HTMLResponse | None:
    """Permission-denied response unless this person may act on the row, else None.

    Allowed when they created it, or when it has no recorded creator at all.
    """
    rows = get_service_client().table(table).select("created_by").eq("id", row_id).execute().data
    created_by = rows[0].get("created_by") if rows else UNOWNED

    if created_by is UNOWNED or created_by == current_user(request)["id"]:
        return None

    return HTMLResponse(
        templates.get_template("partials/permission_denied_modal.html").render(
            {
                "request": request,
                "message": f"Only the person who created this {noun} can rename, archive or delete it.",
            }
        )
    )


def may_act(row: dict | None, user_id: str) -> bool:
    """Template-side mirror of creator_denial, for hiding buttons that would fail.

    Takes an already-fetched row so it costs no query - the sidebar and board
    have these objects in hand. Not a security boundary; creator_denial is.
    """
    if not row:
        return False
    created_by = row.get("created_by")
    return created_by is UNOWNED or created_by == user_id
