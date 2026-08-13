"""Cache-busting URLs for /static assets.

base.html linked /static/styles.css as a bare path, so browsers were free to
keep serving a cached copy indefinitely. That makes a CSS edit land on the
server and stay invisible in the browser - and the failure is worse than "no
change", because freshly rendered HTML gets styled by a stylesheet that predates
it. A new wrapper div whose rule only exists in the new CSS renders completely
unstyled, so the layout breaks in ways that look nothing like either version.

Stamping the file's mtime into the query string makes every edit a distinct URL,
which the browser has to fetch. The stat happens per render rather than once at
import so that editing CSS during development takes effect on reload, with no
server restart.
"""

import os

_STATIC_DIR = os.path.join("app", "static")


def asset_url(path: str) -> str:
    """Return /static/<path> with the file's mtime as a ?v= token."""
    try:
        stamp = int(os.path.getmtime(os.path.join(_STATIC_DIR, path)))
    except OSError:
        # A missing file is the 404 the bare path would have produced anyway;
        # versioning it is not worth raising over during a render.
        return f"/static/{path}"
    return f"/static/{path}?v={stamp}"
