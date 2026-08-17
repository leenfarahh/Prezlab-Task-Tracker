from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.auth import current_user
from app.data import (
    build_board_context,
    build_my_day_context,
    build_sidebar_context,
    build_team_context,
    build_user_context,
)
from app.view_helpers import PRIORITY_COLOR, PRIORITY_LABEL, STATUS_COLOR, STATUS_LABEL, STATUS_ORDER

templates = Jinja2Templates(directory="app/templates")

# The four pages that show task cards, and the fragment each one is refreshed
# through. Scope is carried on the request rather than inferred, because three of
# them cannot be told apart from the board by the workstream/project pair alone -
# and getting it wrong is not cosmetic: team.html and user_detail.html both use
# id="board-container", so an unconditional board refresh swaps a board into the
# middle of the team page.
BOARD = "board"
TEAM = "team"
USER = "user"
MY_DAY = "my_day"
SCOPES = frozenset({BOARD, TEAM, USER, MY_DAY})


def _shared_labels() -> dict:
    return {
        "status_order": STATUS_ORDER,
        "status_label": STATUS_LABEL,
        "status_color": STATUS_COLOR,
        "priority_label": PRIORITY_LABEL,
        "priority_color": PRIORITY_COLOR,
    }


def refreshed_fragments(
    request: Request,
    active_workstream: str,
    active_project: str,
    scope: str = BOARD,
    scope_user: str = "",
) -> str:
    """Renders the page's task view plus the sidebar as out-of-band swaps.

    Everything returned is oob, and that is also what closes the modal: callers
    post with hx-target="#modal-root", so a response whose every element swaps
    itself elsewhere leaves that container empty.

    scope defaults to "board" because every modal-driven write is issued from one
    - the team, profile and my-day pages have no create/edit modals of their own.
    Only a drag sends anything else (see app/static/dnd.js), which is why the
    three other branches exist at all.

    The sidebar is refreshed in every scope: it draws a status dot per task, so a
    drag on any of these pages can change what it shows. Its active-nav key is
    derived here to match what each page's own route passes - notably "profile"
    only on your own profile, "team" on someone else's, same as user_router.
    """
    if scope == TEAM:
        body = templates.get_template("partials/team_list.html").render(
            {"request": request, "oob": True, **_shared_labels(), **build_team_context()}
        )
        nav = TEAM
    elif scope == USER:
        viewer = current_user(request)
        body = templates.get_template("partials/user_detail.html").render(
            {
                "request": request,
                "oob": True,
                "is_self": bool(viewer) and viewer["id"] == scope_user,
                **_shared_labels(),
                **build_user_context(scope_user),
            }
        )
        nav = "profile" if viewer and viewer["id"] == scope_user else TEAM
    elif scope == MY_DAY:
        body = templates.get_template("partials/my_day_sections.html").render(
            {"request": request, "oob": True, **_shared_labels(), **build_my_day_context(current_user(request)["id"])}
        )
        nav = MY_DAY
    else:
        body = templates.get_template("partials/board.html").render(
            {
                "request": request,
                "oob": True,
                **_shared_labels(),
                **build_board_context(active_workstream, active_project),
            }
        )
        nav = active_workstream

    sidebar_scope = active_project if scope == BOARD else "all"
    sidebar_html = templates.get_template("partials/sidebar.html").render(
        {"request": request, "oob": True, **build_sidebar_context(nav, sidebar_scope)}
    )
    return body + sidebar_html
