# Prezlab AI Team task tracker

Same product as the Next.js/React version, rebuilt on a Python backend: FastAPI + Jinja2 + HTMX instead of Next.js/React, same Supabase schema underneath.

See also:
- `docs/stack-decisions-python.md` — what changed vs. the JS version, and the two real trade-offs (polling instead of Realtime, manual session handling instead of `supabase-js`)

**Status: tested, not deployed.** The app has been run locally end-to-end against placeholder credentials: routes all wire up correctly, the login/error paths were exercised and two real bugs were found and fixed in the process (see the stack-decisions doc), and a global error handler was added so a bad key shows a clean message instead of a Python traceback. It has not been run against a real Supabase project, since that needs your own account. The same applies to "New task from text" — the validation and review paths were exercised, but the live Gemini call needs your own API key.

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
SESSION_SECRET=          # generate with: python -c "import secrets; print(secrets.token_hex(32))"
ALLOWED_LOGIN_EMAILS=    # comma-separated; only these addresses can log in
GEMINI_API_KEY=          # optional - powers "New task from text"
GEMINI_MODEL=            # optional - defaults to gemini-3.1-flash-lite
```

`SUPABASE_SERVICE_ROLE_KEY` and `GEMINI_API_KEY` are server-only secrets — set them as environment variables on your host, never commit `.env`.

`SESSION_SECRET` is the only hard requirement: the app refuses to start without it. `ALLOWED_LOGIN_EMAILS` is read the same way — leave it empty and nobody can log in. `GEMINI_API_KEY` is genuinely optional; without it every other feature works and only "New task from text" fails.

## 3. Run locally

```
python -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Visit `http://localhost:8000`. Enter an email that's on `ALLOWED_LOGIN_EMAILS` and you'll land on the board immediately - no code, no password. Anything not on that list is turned away. See "Auth model" below for what that trades away.

## 4. Seed demo data (optional, for review rounds)

After signing in once (so a `profiles` row exists):

```
python scripts/seed.py
```

Creates the same three demo projects, three workstreams, and thirty tasks as the JS version's seed script.

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

## New task from text

The board's "+" menu has a second entry alongside the normal new-task form: paste or type a note in plain language ("Review the Acme deck by Friday, ask Sanad to fix the budget slide") and it comes back as one or more pre-filled task drafts.

How it works: `app/gemini_client.py` sends the note to Gemini along with the current list of workstream and teammate ids, and asks for structured JSON back (title, description, priority, due date, workstream, assignee). `app/routers/nl_task_router.py` then re-validates every id against the real lists before anything is shown, so a hallucinated workstream or assignee id is dropped rather than trusted.

Nothing is written to the database directly from the model. Every run lands on a review screen where each draft can be edited, unchecked, or reassigned, and workstream is required before the batch can be created. That review step is deliberate and shouldn't be optimised away: it's the thing that keeps a wrong guess from silently becoming a real task on someone's board.

## Auth model

There is no identity verification: entering an allow-listed address and submitting logs you in as that address, no code, no password, no proof you own it. Access is gated by `ALLOWED_LOGIN_EMAILS` — an explicit comma-separated list in the environment, which replaced the earlier "any `@prezlab.com` address" domain check. That's only acceptable because this app is reachable solely on the enterprise network, not the public internet - never widen the allow-list to a whole domain or expose this app publicly without adding real verification back.

Login sets two things: a signed session cookie (`{id, email, full_name}`, short-lived) and a `refresh_token` cookie backed by the `auth_tokens` table (`supabase/schema.sql`), which lets the session survive the signed cookie expiring without re-entering an email. Each refresh token is stored as a hash with an `expires_at` and a `revoked_at`; logging out revokes the current one. To force-end a specific teammate's sessions server-side (e.g. an offboarding), set `revoked_at = now()` on their rows in `auth_tokens` from the Supabase SQL editor - there's no admin UI for it yet.

## Known limitations (stated plainly, not buried)

### Access and permissions

- **Logging in proves nothing.** An allow-listed address gets in with no code and no password, so anyone who can reach the app and knows a listed address can sign in as that person. The allow-list narrows *who* can get in; it does nothing to verify *that they are who they claim*. This holds only as long as the app stays off the public internet.
- **The allow-list is read once, at startup.** `ALLOWED_LOGIN_EMAILS` is parsed when `app/config.py` is imported, so adding or removing someone means editing the environment **and restarting the process** — it is not a live setting. Removing an address also doesn't end that person's existing session; revoke their rows in `auth_tokens` too (see "Auth model" above) or they stay signed in until the token expires.
- **Any signed-in teammate can see and edit everything** — same as the JS version, and same caveat: if per-workstream access control is needed later, it now has to be added as explicit checks in the FastAPI routes (see `docs/stack-decisions-python.md`), since RLS is bypassed by this client's use of the service role key.

### "New task from text" (Gemini)

- **Prompt text leaves our infrastructure.** Whatever gets typed into that box is sent to Google's Generative Language API, along with the names of every active workstream and every teammate. Client names, deadlines, and anything else pasted in go with it. That is the real cost of this feature, and it should be an explicit decision before this is used on client work, not a footnote.
- **The model's guesses are guesses.** Workstream, assignee, priority, and due date are inferred, and it will get some of them wrong — particularly when a note mentions a client that maps to no workstream, or a name that isn't on the team. Invented ids are dropped server-side, an unrecognised assignee falls back to whoever is logged in, and an unmatched workstream comes back blank and has to be picked by hand. The review screen exists because of this; don't build a "create without reviewing" shortcut on top of it.
- **No key, no feature.** With `GEMINI_API_KEY` unset the modal returns a plain error instead of hiding itself, so people will find the entry point before they find out it isn't configured. Everything else in the app keeps working.
- **One synchronous call, no retry.** The request blocks for up to 30 seconds with no streaming and no progress indicator, and a timeout or a transient API error surfaces as an error message the person has to resubmit from. Long notes producing many drafts are the slow case.
- **No spend controls.** There's no rate limit, no per-user quota, and no cap on prompt length beyond the browser's — a pasted wall of text is sent as-is. Set budget alerts on the Google AI Studio key rather than relying on the app to restrain itself.
- **The default model id is unverified.** `GEMINI_MODEL` defaults to `gemini-3.1-flash-lite`. Model names get revised and retired; confirm it still exists at ai.google.dev before deploying, or the feature fails at the first call.

### Data and behaviour

- **Board updates via polling, not push.** The board and sidebar refresh every 5-6 seconds, the team panel every 10. There's a few seconds of lag between someone else moving a task and it showing up for you — not instant like the JS version's websocket-based Realtime. Fine for a small team's daily use; worth revisiting if that lag becomes a real complaint.
- **Unarchiving a project is not an exact undo.** Archiving a project archives its workstreams and tasks as one unit, and unarchiving reverses that across *all* of them. Anything that was archived individually *before* the project was archived comes back out too. Rare, but surprising when it happens.
- **`docs/llm-feature-proposal.md` describes a feature this version doesn't have.** It argues for the JS version's Workstream Pulse digest; the LLM feature actually built here is "New task from text". Read it as background on the decision, not as a description of the app.
- **No comments/notifications** — same scope cut as the JS version, contained follow-ups rather than schema changes.

Flag any of these in a review round if they turn out to matter more than expected. Most are contained changes rather than rewrites — the exception is the Gemini data-sharing point, which is a decision to make rather than a bug to fix.
