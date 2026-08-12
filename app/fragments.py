from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.data import build_board_context, build_sidebar_context
from app.view_helpers import PRIORITY_COLOR, PRIORITY_LABEL, STATUS_COLOR, STATUS_LABEL, STATUS_ORDER

templates = Jinja2Templates(directory="app/templates")


def refreshed_fragments(request: Request, active_workstream: str, active_project: str) -> str:
    """Renders board + sidebar as out-of-band swaps, closing the modal in the process."""
    board_ctx = {
        "request": request,
        "status_order": STATUS_ORDER,
        "status_label": STATUS_LABEL,
        "status_color": STATUS_COLOR,
        "priority_label": PRIORITY_LABEL,
        "priority_color": PRIORITY_COLOR,
        "oob": True,
        **build_board_context(active_workstream, active_project),
    }
    sidebar_ctx = {"request": request, "oob": True, **build_sidebar_context(active_workstream, active_project)}

    board_html = templates.get_template("partials/board.html").render(board_ctx)
    sidebar_html = templates.get_template("partials/sidebar.html").render(sidebar_ctx)
    return board_html + sidebar_html
