# Stack decisions: Pulse, Python version

This is a from-scratch rebuild of the same product on a Python backend, at your request, alongside the original Next.js/React version. The database layer, schema, and the LLM feature's design are unchanged; what changes is everything above the database.

## Summary

| Layer | Choice | Why not the JS-version equivalent |
|---|---|---|
| Backend/app framework | FastAPI | Python's closest equivalent to Next.js's API routes: async, typed, minimal ceremony |
| Frontend rendering | Jinja2 templates, server-rendered | No React/Node in this version at all - avoids running two language runtimes for one app |
| Interactivity | HTMX (partial-page swaps over plain HTTP) | Gets "feels live" behavior without hand-writing a JS SPA or a websocket client |
| "Live" board updates | HTMX polling every 5-6s | Supabase Realtime's officially supported client is JS; Python's realtime support is thinner. Polling is a legitimate, simpler trade-off for a small internal tool - see below |
| Database | Supabase Postgres (same schema, same file) | Unchanged - the DB layer doesn't care what language queries it |
| Auth | Supabase Auth (email OTP code), session state in a signed cookie | Supabase's browser SDK handles session refresh/JWT-per-request automatically in JS. This version does that manually - see the trade-off note below |
| Hosting | Any Python host: Render, Fly.io, Railway, or a VM behind Caddy/nginx | Vercel's Python support exists but is secondary; a Python app is more naturally hosted where Python is the first-class runtime |

## Why FastAPI + Jinja2 + HTMX, specifically

Once the decision is "no Node," the honest options for the frontend were: a Python templating engine with plain HTML forms (basic, but a worse day-to-day experience than what the JS version has - no live updates, no modals without full page reloads), or a Python templating engine plus a small library that adds interactivity without writing custom JavaScript. HTMX is that second option: the whole app - board polling, opening a task in a modal, saving an edit, closing the modal - is expressed as HTML attributes (`hx-get`, `hx-post`, `hx-target`, `hx-swap`) rather than a client-side JS framework. That keeps the entire interactive layer inside Python + Jinja templates, which was the actual point of doing a Python version.

**FastAPI over Flask/Django.** FastAPI's async support and automatic OpenAPI docs (visible at `/docs` once running) make it a comparable weight to Next.js API routes conceptually, and its typed request/response handling caught real bugs during testing (see "What broke and got fixed" below) faster than an untyped Flask app would have.

## The two trade-offs this version makes, stated plainly

**1. Realtime becomes polling.** The JS version subscribes to Postgres changes over a websocket and the board updates the instant anyone else changes a task. This version has the board and sidebar poll the server every 5-6 seconds via HTMX (`hx-trigger="every 5s"`). For a small internal team looking at a shared board, a few seconds of staleness is a reasonable trade for not having to hand-roll or bolt on a websocket client in Python. If this becomes a real problem in practice (fast-moving standups, lots of simultaneous editors), the fix is either Supabase's realtime-py client directly, or moving the polling interval down further, not a full rearchitecture.

**2. Auth session handling is manual, not automatic.** In the JS version, `supabase-js` in the browser handles token storage and refresh, and Postgres Row Level Security evaluates every query as the actual signed-in user. This version does the OAuth/OTP code exchange server-side, then stores only `{id, email, full_name}` in a signed session cookie, and uses the Supabase **service role key** for all data access afterward (bypassing RLS at the database level, gatekept instead by a `require_login` check in FastAPI - see `app/supabase_client.py` for the full reasoning written into the code).

This is a safe trade-off **only** because the current RLS policy already grants any authenticated teammate full access - the app-level check and the database-level policy currently enforce the same rule from two different layers. If per-workstream or per-role restrictions get added later, that logic now has to live in the FastAPI routes, since the database is no longer doing that enforcement for this client. Flag this explicitly if/when that need comes up - it's a deliberate simplification for v1, not an oversight.

## What broke and got fixed during the build (kept here on purpose)

Two things surfaced in testing that are worth being upfront about rather than presenting a "it just worked" prototype:

- The Supabase client was being created *outside* the `try/except` in the login and callback routes, so a misconfigured or invalid key produced a raw 500 instead of a graceful error redirect. Fixed by moving client creation inside the try block.
- There was no top-level error boundary, so any unhandled backend failure (expired key, Supabase project paused, network blip) would have shown a Python stack trace directly to whoever was using the app. Added a global FastAPI exception handler (`app/main.py`) that logs the real error server-side and shows a plain "something didn't load" page to the person using it.

Both were caught by actually running the app against placeholder credentials and checking the failure paths, not just the happy path - worth doing the same check again once real credentials are in place, since a valid-but-wrong key (e.g. pointed at the wrong Supabase project) will fail in the same way.

## What's identical to the JS version

- `supabase/schema.sql` - byte-for-byte the same file.
- The LLM feature (`docs/llm-feature-proposal.md`) - same recommendation, same system prompt logic, same measurement plan. Only the HTTP call moved from `fetch` to `httpx`.
- The visual design tokens (colors, the health-strip signature element) - reproduced in plain CSS instead of Tailwind, since there's no JS build step in this version.
