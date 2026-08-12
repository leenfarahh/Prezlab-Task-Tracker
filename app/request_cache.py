"""Per-request memo for Supabase reads.

Rendering one page asks for the same tables several times over - the sidebar
context and the board context each build their own view of
projects/workstreams/tasks - so a dashboard load was issuing 8 queries to read
4 distinct things. Every one of those is a separate HTTPS round trip, and with
payloads this small the wait is almost entirely network, so the repeats were
pure latency.

Two rules make this safe to cache:

  1. Scope is a single request. The cache is created by RequestCacheMiddleware
     and thrown away when the response is done, so nothing survives between
     users or between polls.
  2. Any write empties it. get_service_client() calls invalidate() after every
     successful non-GET to Supabase, so a route that updates a row and then
     re-renders reads the new value, not the one it happened to read earlier
     in the same request. Doing it there rather than in each route means it
     cannot be forgotten when a new write is added.

Outside a request - scripts/seed.py, a REPL - no cache is installed and every
fetch goes straight to the database, exactly as before.
"""

import contextvars
from concurrent.futures import ThreadPoolExecutor

_cache: contextvars.ContextVar[dict | None] = contextvars.ContextVar("supabase_request_cache", default=None)


class RequestCacheMiddleware:
    """Gives each HTTP request its own empty cache.

    Written as raw ASGI rather than a Starlette @app.middleware("http")
    function on purpose: BaseHTTPMiddleware runs the rest of the app in a
    separate task, and context propagation across that boundary is the kind of
    thing that works until it quietly doesn't. Plain ASGI middleware runs in
    the same task as the endpoint, so the ContextVar set here is guaranteed
    visible to the route that needs it.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = _cache.set({})
        try:
            await self.app(scope, receive, send)
        finally:
            _cache.reset(token)


def memo(key: str, loader):
    """Returns the cached value for `key`, calling `loader` on first ask."""
    cache = _cache.get()
    if cache is None:
        return loader()
    if key not in cache:
        cache[key] = loader()
    return cache[key]


def invalidate() -> None:
    """Drops everything read so far this request. Called after every write."""
    cache = _cache.get()
    if cache is not None:
        cache.clear()


def prefetch(*loaders) -> None:
    """Warms the cache with several independent reads at once.

    None of these queries depends on another, so issuing them one at a time
    just stacks up round trips - the single largest source of page latency
    here. Each worker gets its own copy_context() because a bare thread starts
    with an empty context, which would leave memo() writing into nothing and
    every caller re-fetching afterwards. The copies share the same cache dict,
    so results land where the request can see them.
    """
    if _cache.get() is None or len(loaders) < 2:
        return
    with ThreadPoolExecutor(max_workers=len(loaders)) as pool:
        for future in [pool.submit(contextvars.copy_context().run, fn) for fn in loaders]:
            future.result()
