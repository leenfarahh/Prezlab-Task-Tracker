"""
Supabase access layer.

Design note (see docs/stack-decisions-python.md for the full reasoning): the
JS version passes the signed-in user's own JWT into supabase-js on every
request, so Postgres Row Level Security evaluates as that specific user.
supabase-py's auth/session handling for that per-request pattern is less
mature, so this version takes a simpler, explicit approach instead: FastAPI
checks for a valid session cookie before any route runs (see app/auth.py),
and once that check passes, all data access uses the service-role key, which
bypasses RLS entirely at the database level.

This is a safe trade-off ONLY because the RLS policies in schema.sql already
grant any authenticated user full read/write access - the app-level check and
the database-level policy currently express the same rule. If per-workstream
or per-role access control is added later, that logic has to live here, in
the FastAPI routes, since the database will no longer be doing that
enforcement for this client.
"""
from functools import lru_cache

import httpx
from supabase import Client, create_client

from app.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL


class _RetryOnDisconnectTransport(httpx.HTTPTransport):
    """Retries once if a pooled connection turns out to be dead.

    Supabase's edge occasionally closes an idle HTTP/2 keep-alive connection
    between this app's periodic polling requests (the board/sidebar poll every
    5-6s). httpx's own `retries=` option only covers the initial TCP connect,
    not a connection reused from the pool that's then found to be closed, so
    without this it surfaces as a hard 500 on whichever poll loses the race.
    Only safe/idempotent methods are retried - a write could have already
    reached the server before the connection dropped.
    """

    _SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return super().handle_request(request)
        except httpx.RemoteProtocolError:
            if request.method not in self._SAFE_METHODS:
                raise
            return super().handle_request(request)


@lru_cache
def get_service_client() -> Client:
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    # See _RetryOnDisconnectTransport docstring.
    client.postgrest.session._transport = _RetryOnDisconnectTransport(http2=True)
    return client
