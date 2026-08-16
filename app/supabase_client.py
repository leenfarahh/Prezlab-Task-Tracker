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
import time
from functools import lru_cache

import httpx
from supabase import Client, create_client

from app.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from app.request_cache import invalidate as _invalidate_request_cache


class _RetryOnDisconnectTransport(httpx.HTTPTransport):
    """Retries a request whose connection failed rather than whose server did.

    Two classes of failure land here, and they are not equally safe to retry.
    What separates them is whether any bytes of the request reached Supabase.

    Before the request was sent - ConnectError, ConnectTimeout, PoolTimeout. The
    socket was refused or reset, the TLS handshake stalled, or no connection came
    free in time. Supabase's edge does the first two when several new handshakes
    arrive together, which is what a cold pool produces now that each request
    issues its reads in parallel. Nothing was transmitted, so *every* method is
    safe to retry here, POST included: an insert that never left this process
    cannot have landed twice.

    After it may have been sent - RemoteProtocolError. An established connection
    died mid-exchange, most often an idle keep-alive one the peer closed between
    polls. The request may or may not have arrived, so the method matters. PATCH
    and DELETE are retried alongside the reads because every .update()/.delete()
    in this app (see app/routers/*.py) sets fields to a fixed literal or removes
    a row by id, so re-sending changes nothing if the first attempt did land.
    POST is excluded: retrying an .insert() that secretly succeeded would create
    a duplicate row.

    httpx's own `retries=` option covers neither case usefully - it only retries
    the initial connect, and never reaches a connection reused from the pool.

    Three attempts with a short, growing pause. A burst-induced reset clears well
    inside a second, and anything still failing after that is a real outage the
    caller should see rather than wait on.
    """

    # Nothing reached the wire, so repeating cannot duplicate anything.
    _BEFORE_SEND = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
    _RETRYABLE_MID_FLIGHT = {"GET", "HEAD", "OPTIONS", "PATCH", "DELETE"}
    _READ_METHODS = {"GET", "HEAD", "OPTIONS"}
    _ATTEMPTS = 3

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(self._ATTEMPTS):
            final = attempt == self._ATTEMPTS - 1
            try:
                response = super().handle_request(request)
                break
            except self._BEFORE_SEND:
                if final:
                    raise
            except httpx.RemoteProtocolError:
                if final or request.method not in self._RETRYABLE_MID_FLIGHT:
                    raise
            time.sleep(0.1 * (2**attempt))

        # Any successful write makes whatever this request has already read
        # potentially stale - most visibly on routes that update a row and
        # then re-render from it (POST /users/{id} would otherwise redisplay
        # the name the auth check happened to read a moment earlier). Clearing
        # here rather than in each route means it can't be forgotten when a
        # new write is added. See app/request_cache.py.
        if request.method not in self._READ_METHODS and response.status_code < 400:
            _invalidate_request_cache()
        return response


# httpx defaults to keepalive_expiry=5s, which is shorter than this app's own
# polling intervals (board 5s, sidebar 6s, team 10s, bell 20s). Every idle
# connection therefore lapsed between one poll and the next, so nearly every
# poll - and every click that followed a few seconds of reading - paid a fresh
# TLS handshake to Supabase before it could ask anything. Measured at ~135ms per
# lapsed connection, and a board render opens four in parallel.
#
# A minute covers every interval above three times over while staying short
# enough to be unlikely to outlive the peer's own idle timeout. Holding a
# connection at all is safe here for the same reason the pool runs over HTTP/1.1
# (see below): httpcore checks whether an idle socket has become readable, which
# can only mean the peer hung up, and discards it rather than handing it out.
#
# max_connections is a ceiling on how many handshakes can be in flight at once,
# and the default of 100 is far too loose for this app's shape. A page issues
# four reads in parallel, so a browser with the board open - page, board poll,
# sidebar poll and bell all overlapping - can ask for a dozen connections in the
# same instant, and against a cold pool every one of those is a fresh TLS
# handshake. Supabase's edge answers a burst that size by resetting or stalling
# some of them, which is what surfaced as intermittent 500s.
#
# Sixteen is comfortably above what one person's open tabs need in parallel and
# low enough that a burst queues briefly on a warm connection instead of opening
# another. Queuing is nearly free once the pool is warm; a handshake is not.
#
# max_keepalive matches it, so every connection the pool opens is one it can
# hand back out. A smaller keepalive count would just churn - closing warm
# connections only to dial fresh ones, which is the cost this whole block exists
# to avoid.
_LIMITS = httpx.Limits(max_connections=16, max_keepalive_connections=16, keepalive_expiry=60.0)

# supabase-py hands its postgrest session a single blanket timeout of 120s,
# applied to every phase. That is not a timeout so much as the absence of one:
# a TLS handshake that stalls - which is how Supabase's edge sometimes answers a
# burst of new connections - held the request for two full minutes before
# failing, and with the retry above that becomes six. A poll that hangs for two
# minutes is worse for the person using this than an error would be.
#
# Split by phase instead, because the phases mean different things. A handshake
# that has not completed in 5s is not going to, and giving up on it quickly is
# what makes retrying useful rather than additive. A read is allowed far longer:
# these queries answer in well under a second, so 20s is already an outage, but
# it is the phase where waiting can still pay off. Pool is the wait for a free
# connection, bounded by the ceiling above.
#
# Worst case is now three attempts of ~5s plus backoff rather than an unbounded
# stall, and the common case is untouched.
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


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
    client.postgrest.session._transport = _RetryOnDisconnectTransport(limits=_LIMITS)
    client.postgrest.session.timeout = _TIMEOUT
    return client
