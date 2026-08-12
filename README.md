# Prezlab AI Team task tracker

Same product as the Next.js/React version, rebuilt on a Python backend: FastAPI + Jinja2 + HTMX instead of Next.js/React, same Supabase schema underneath.

See also:
- `docs/stack-decisions-python.md` — what changed vs. the JS version, and the two real trade-offs (polling instead of Realtime, manual session handling instead of `supabase-js`)

**Status: tested, not deployed.** The app has been run locally end-to-end against placeholder credentials: routes all wire up correctly, the login/error paths were exercised and two real bugs were found and fixed in the process (see the stack-decisions doc), and a global error handler was added so a bad key shows a clean message instead of a Python traceback. It has not been run against a real Supabase project, since that needs your own account.

## 1. Create the Supabase project

Same as the JS version:

1. Create a project at supabase.com.
2. Run `supabase/schema.sql` in the SQL editor.
3. Copy the Project URL and `service_role` key from Project Settings → API.

## 2. Configure environment variables

```
cp .env.example .env
```

Fill in:

```
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SESSION_SECRET=      # generate with: python -c "import secrets; print(secrets.token_hex(32))"
```

`SUPABASE_SERVICE_ROLE_KEY` is a server-only secret — set it as an environment variable on your host, never commit `.env`.

## 3. Run locally

```
python -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Visit `http://localhost:8000`. Enter any `@prezlab.com` email and you'll land on the board immediately - no code, no password. See "Auth model" below for what that trades away.

## 4. Seed demo data (optional, for review rounds)

After signing in once (so a `profiles` row exists):

```
python scripts/seed.py
```

Creates the same three demo workstreams and thirty tasks as the JS version's seed script.

## 5. Deploy

This is a plain ASGI app, so it runs on any Python host. Two straightforward options:

**Render / Railway / Fly.io** (recommended for simplicity):
1. Push this repo to GitHub.
2. Create a new web service pointing at the repo.
3. Build command: `pip install -r requirements.txt`. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
4. Add the environment variables from step 2 in the host's dashboard.

**A VM behind a reverse proxy** (if Prezlab already runs infrastructure this way): run Uvicorn behind Caddy or nginx with a systemd service, same environment variables.

## Review round workflow

Same as the JS version: share a deployed preview link for round 1, collect feedback, iterate, share an updated link for round 2, sign off, deploy to the production URL.

## Auth model

There is no identity verification: entering `you@prezlab.com` and submitting logs you in as that address, no code, no password, no proof you own it. That's only acceptable because this app is reachable solely on the enterprise network, not the public internet - never relax the domain check or expose this app publicly without adding real verification back.

Login sets two things: a signed session cookie (`{id, email, full_name}`, short-lived) and a `refresh_token` cookie backed by the `auth_tokens` table (`supabase/schema.sql`), which lets the session survive the signed cookie expiring without re-entering an email. Each refresh token is stored as a hash with an `expires_at` and a `revoked_at`; logging out revokes the current one. To force-end a specific teammate's sessions server-side (e.g. an offboarding), set `revoked_at = now()` on their rows in `auth_tokens` from the Supabase SQL editor - there's no admin UI for it yet.

## Known limitations (stated plainly, not buried)

- **Board updates via polling, not push.** The board and sidebar refresh every 5-6 seconds. There's a few seconds of lag between someone else moving a task and it showing up for you — not instant like the JS version's websocket-based Realtime. Fine for a small team's daily use; worth revisiting if that lag becomes a real complaint.
- **Any signed-in teammate can see and edit everything** — same as the JS version, and same caveat: if per-workstream access control is needed later, it now has to be added as explicit checks in the FastAPI routes (see `docs/stack-decisions-python.md`), since RLS is bypassed by this client's use of the service role key.
- **No comments/notifications** — same scope cut as the JS version, contained follow-ups rather than schema changes.

Flag any of these in a review round if they turn out to matter more than expected — none of them are hard to change, they're just not v1.
