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
the database-level policy currently express the same rule. If per-project
or per-role access control is added later, that logic has to live here, in
the FastAPI routes, since the database will no longer be doing that
enforcement for this client.
"""
from functools import lru_cache

import httpx
from supabase import Client, create_client

from app.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from app.request_cache import invalidate as _invalidate_request_cache


class _RetryOnDisconnectTransport(httpx.HTTPTransport):
    """Retries once if a pooled connection turns out to be dead.

    Supabase's edge occasionally closes an idle keep-alive connection between
    this app's periodic polling requests (the board/sidebar poll every 5-6s).
    httpx's own `retries=` option only covers the initial TCP connect, not a
    connection reused from the pool that's then found to be closed, so without
    this it surfaces as a hard 500 on whichever request loses the race.

    PATCH and DELETE are included alongside the read methods because every
    .update()/.delete() call in this app (see app/routers/*.py) sets fields to
    a fixed literal value or removes a row by id - re-sending an identical
    request changes nothing if the first attempt actually landed. POST is
    deliberately excluded: retrying an .insert() that secretly succeeded
    would create a duplicate row.

    This is the second line of defence only. The first is running the pool
    over HTTP/1.1 (see get_service_client), which is what keeps POST - the one
    method that can't be retried - out of trouble.
    """

    _SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "PATCH", "DELETE"}
    _READ_METHODS = {"GET", "HEAD", "OPTIONS"}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            response = super().handle_request(request)
        except httpx.RemoteProtocolError:
            if request.method not in self._SAFE_METHODS:
                raise
            response = super().handle_request(request)

        # Any successful write makes whatever this request has already read
        # potentially stale - most visibly on routes that update a row and
        # then re-render from it (POST /users/{id} would otherwise redisplay
        # the name the auth check happened to read a moment earlier). Clearing
        # here rather than in each route means it can't be forgotten when a
        # new write is added. See app/request_cache.py.
        if request.method not in self._READ_METHODS and response.status_code < 400:
            _invalidate_request_cache()
        return response


@lru_cache
def get_service_client() -> Client:
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    # HTTP/1.1 on purpose, and it is load-bearing rather than a default left
    # alone. httpcore's HTTP/1.1 connection reports has_expired() == True when
    # an idle socket has become readable, which can only mean the peer closed
    # it, so the pool discards it and dials a fresh one. Its HTTP/2 connection
    # only ever compares against the keep-alive deadline and has no equivalent
    # check, so a connection Supabase had already closed got handed out and
    # failed mid-read with RemoteProtocolError("Server disconnected"). That is
    # survivable for a retryable verb but not for an .insert(): it took down
    # POST /tasks/batch with a 500. Nothing here needs multiplexing (postgrest
    # calls are sequential), and the auth and storage clients already run over
    # HTTP/1.1, so this only brings postgrest in line with them.
    # See _RetryOnDisconnectTransport for the remaining retry layer.
    client.postgrest.session._transport = _RetryOnDisconnectTransport()
    return client
