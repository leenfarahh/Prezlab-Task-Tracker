# Prezlab AI Team task tracker

Same product as the Next.js/React version, rebuilt on a Python backend: FastAPI + Jinja2 + HTMX instead of Next.js/React, same Supabase schema underneath.

Work is organised as **Workstream → Project → Task**: a workstream is the standing area of work (a client, a product, an internal track), a project is a single piece of delivery inside it, and tasks attach to a project. Archiving cascades downward — archiving a workstream archives its projects and their tasks as one unit.

See also:
- `docs/stack-decisions-python.md` — what changed vs. the JS version, and the two real trade-offs (polling instead of Realtime, manual session handling instead of `supabase-js`)

**Status: tested, not deployed.** The app has been run locally end-to-end against placeholder credentials: routes all wire up correctly, the login/error paths were exercised and two real bugs were found and fixed in the process (see the stack-decisions doc), and a global error handler was added so a bad key shows a clean message instead of a Python traceback. It has not been run against a real Supabase project, since that needs your own account. The same applies to "New task from text" — the validation and review paths were exercised, but the live Gemini call needs your own API key.

## 1. Create the Supabase project

Same as the JS version:

1. Create a project at supabase.com.
2. Run `supabase/schema.sql` in the SQL editor.
3. Copy the Project URL and `service_role` key from Project Settings → API.

Re-running `schema.sql` on a database that predates the **Workstream → Project → Task** hierarchy (an earlier revision nested it the other way round) migrates it in place: the two tables and their child columns are renamed, so every existing row and task survives — what used to be a top-level project becomes a workstream, and each workstream it contained becomes one of that workstream's projects. The block is guarded, so it does nothing on a fresh project and is safe to re-run.

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

Creates the same three demo workstreams, three projects, and thirty tasks as the JS version's seed script.

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

The strip above the board — top right, next to your profile avatar — carries a second button alongside the normal new-task form: paste or type a note in plain language ("Review the Acme deck by Friday, ask Sanad to fix the budget slide") and it comes back as one or more pre-filled task drafts.

How it works: `app/gemini_client.py` sends the note to Gemini along with the current list of project and teammate ids, and asks for structured JSON back (title, description, priority, due date, project, assignee). `app/routers/nl_task_router.py` then re-validates every id against the real lists before anything is shown, so a hallucinated project or assignee id is dropped rather than trusted.

Nothing is written to the database directly from the model. Every run lands on a review screen where each draft can be edited, unchecked, or reassigned, and project is required before the batch can be created. That review step is deliberate and shouldn't be optimised away: it's the thing that keeps a wrong guess from silently becoming a real task on someone's board.

## Moving a task between statuses

Two ways, both kept: open the card and change the Status field, or drag the card into another column. The drag posts to `POST /tasks/{id}/status` (status only, not the whole task) and comes back as the same board+sidebar out-of-band refresh every other write uses. Same permission rule as the modal — creator or assignee only — so a refused drag returns the permission modal and the card snaps back.

Every status column is ordered by due date, soonest first, with undated tasks at the bottom (`due_date_sort_key` in `app/view_helpers.py`, applied by `group_by_status` — so the board, the team page, and profile pages all read the same way). A dropped card is placed into that order client-side too, rather than at the end of the column, so it doesn't visibly jump when the refresh lands.

Dragging is enabled only on the status-grouped board. The "by user" columns are people rather than statuses, and the archived board is a history view, so cards in both stay click-to-edit. Client side lives in `app/static/dnd.js`.

## Comments

Every task carries a comment thread at the bottom of its edit modal, stored in `task_comments` (`supabase/schema.sql`).

The permission rule here is deliberately **not** the one the rest of the task routes use. Editing, archiving, deleting, and dragging a task are all creator-or-assignee only (`_task_owner_denial`), but **any signed-in teammate can comment on any task** — that's the point of the feature, since commenting is how you raise a question on work that isn't yours. Deleting a comment is restricted to that comment's own author, enforced as part of the delete filter (`.eq("id", …).eq("author_id", …)`) rather than by reading the row first, so there's no gap between the check and the write. A task's owner cannot delete a teammate's remark.

Posting or deleting re-renders only the thread (`#task-comments`), not the board — comments don't change anything the board draws, and a full refresh would close the modal and discard whatever was half-typed in the edit form above. Comments are hard-deleted along with their task via `on delete cascade`.

## Notifications and the activity page

A bell in the top bar shows how many updates you haven't seen. Your own actions never count — archiving your own task can't light up your own bell.

`/activity` shows **everything** by default, with a **Show** filter to narrow to "My tasks". Everything is the default deliberately: narrowing relies on the `audience` recorded on each event (creator, assignee, actor), and that is only as good as the data behind it — a task with no `created_by` and no `assignee_id`, which seeded or imported rows often are, produces an event nobody is the audience for. Under an audience-only feed those events existed but appeared to no one.

`/activity` shows the full stream: creations, assignments, edits, status moves, completions, archives, deletions and comments. Events are written by the application (`app/activity_log.py`), not by a database trigger. The trigger that used to do it has been dropped, because it could not know two things: **who acted** (every query uses one service-role key with no per-request database user, so it logged the task's *assignee* as the actor — reassigning someone else's task credited them with having done it), and **what the action meant** (at the row level an archive, an edit and a status change are all the same UPDATE).

Each event carries a snapshot — task title, project name, and the `audience` it belongs to (creator, assignee, actor, as of that moment). The feed filters on that array rather than joining back to tasks, which is what lets a **deleted** task still appear, and freezes whose history an event belongs to: reassigning a task later doesn't move its past out of the previous owner's feed. `task_activity.task_id` is now nullable with `on delete set null`; a deletion event is written with a null `task_id` outright, since inserting a reference to an already-deleted row would fail the foreign key.

The feed covers **workstreams and projects** as well as tasks — created, renamed, archived, unarchived, deleted. `entity_type`/`entity_id`/`entity_title` is the shared triple everything reads back through; the task-shaped columns stay because rows written earlier still use them and because `task_id` is what opens a task modal. A workstream or project event reaches everyone with a task under it plus the actor, so archiving or deleting one lands in the feed of the people whose work went with it; creating an empty one reaches only its creator, since there is nobody else it affects yet.

Running `schema.sql` backfills the history that can be reconstructed: one `commented` event per existing comment (keeping the comment's own timestamp, so the feed reads in order), and one `created` event per existing task, project and workstream. Task creations carry a real actor from `created_by`; **projects and workstreams don't store one**, so those show as "Someone" rather than a guess. Every insert is idempotent — comment events keyed by `source_comment_id` behind a unique index, creations by "does this entity already have a `created` event" — so re-running never duplicates, including for events the app has written since the last run.

**Nothing else can be backfilled**, and the reason is worth knowing before anyone tries to add it: archives store a boolean with no timestamp and no actor (`tasks.updated_at` is the time of the *last* change of any kind, so it would date an archive to whenever the task was last edited, and projects/workstreams have no `updated_at` at all); edits, status changes and reassignments keep only the current value; and deleted rows are simply gone. Inventing timestamps or actors would put wrong history in front of people who would reasonably trust it.

The page filters by **person**, **activity type**, and **project**, and sorts newest- or oldest-first. Filters are applied in the query rather than to the fetched page — post-filtering would only ever search the most recent 100 events, so asking for a rare type or a quiet project would come back empty whenever newer unrelated rows had already filled the cap, which reads as "nothing happened" rather than "nothing shown". It's a plain GET form, so each filtered view is its own bookmarkable URL. Project filtering uses a `project_id` snapshot on the event (a bare uuid, no foreign key — a reference would either delete the row with the project or null the value being filtered on). The bell count is always unfiltered.

One remaining gap: deleting a *project or workstream* removes its tasks without logging a per-task deletion for each one.

The bell loads and refreshes itself (`hx-trigger="load, every 20s"` on the wrapper in `dashboard.html`) rather than being handed a count. That top strip is shared by every page — board, team, profile, archived, activity — each rendered by a different route, so threading a count through context would mean touching all of them and remembering to on the next one.

`/activity` lists those events newest-first, entries you hadn't seen yet tinted. **It is private.** The route takes no user id at all and builds the feed for whoever is in the session, so there is no `/users/{id}/activity` and no id to swap for a teammate's — the privacy comes from the route's shape rather than from a check a later edit could drop. Nobody can see anyone else's activity.

Unread is tracked by one watermark per person, `profiles.comments_seen_at`; opening `/activity` pushes it to `now()`. The bump happens *after* the response is rendered, so the page you're looking at still highlights what was new — that highlight survives exactly one visit. Two consequences worth knowing: the watermark is a single timestamp, not per-comment read state, so opening the page marks *everything* read at once; and archived tasks are excluded, since their comments are history and including them would cost a second query on every bell poll.

## Auth model

There is no identity verification: entering an allow-listed address and submitting logs you in as that address, no code, no password, no proof you own it. Access is gated by `ALLOWED_LOGIN_EMAILS` — an explicit comma-separated list in the environment, which replaced the earlier "any `@prezlab.com` address" domain check. That's only acceptable because this app is reachable solely on the enterprise network, not the public internet - never widen the allow-list to a whole domain or expose this app publicly without adding real verification back.

Login sets two things: a signed session cookie (`{id, email, full_name}`, short-lived) and a `refresh_token` cookie backed by the `auth_tokens` table (`supabase/schema.sql`), which lets the session survive the signed cookie expiring without re-entering an email. Each refresh token is stored as a hash with an `expires_at` and a `revoked_at`; logging out revokes the current one. To force-end a specific teammate's sessions server-side (e.g. an offboarding), set `revoked_at = now()` on their rows in `auth_tokens` from the Supabase SQL editor - there's no admin UI for it yet.

## Known limitations (stated plainly, not buried)

### Access and permissions

- **Logging in proves nothing.** An allow-listed address gets in with no code and no password, so anyone who can reach the app and knows a listed address can sign in as that person. The allow-list narrows *who* can get in; it does nothing to verify *that they are who they claim*. This holds only as long as the app stays off the public internet.
- **The allow-list is read once, at startup.** `ALLOWED_LOGIN_EMAILS` is parsed when `app/config.py` is imported, so adding or removing someone means editing the environment **and restarting the process** — it is not a live setting. Removing an address also doesn't end that person's existing session; revoke their rows in `auth_tokens` too (see "Auth model" above) or they stay signed in until the token expires.
- **Any signed-in teammate can see everything**, and can edit any *task* they created or are assigned. Workstreams and projects are creator-only for rename, archive, unarchive and delete (`app/permissions.py`); tasks use creator-or-assignee (`_task_owner_denial`). If per-workstream access control is needed later, it has to be added as explicit checks in the FastAPI routes (see `docs/stack-decisions-python.md`), since RLS is bypassed by this client's use of the service role key.
- **Projects and workstreams created before `created_by` existed have no recorded creator**, and are treated as unowned — anyone can rename, archive or delete them. The column was added when delete was; it is not backfilled, because who created them was never recorded and guessing an owner would be worse than admitting the gap. Anything created from now on is creator-only.

### "New task from text" (Gemini)

- **Prompt text leaves our infrastructure.** Whatever gets typed into that box is sent to Google's Generative Language API, along with the name of every active project, the workstream each one sits in, and every teammate. Client names, deadlines, and anything else pasted in go with it. That is the real cost of this feature, and it should be an explicit decision before this is used on client work, not a footnote.
- **The model's guesses are guesses.** Project, assignee, priority, and due date are inferred, and it will get some of them wrong — particularly when a note mentions a client that maps to no project, or a name that isn't on the team. Invented ids are dropped server-side, an unrecognised assignee falls back to whoever is logged in, and an unmatched project comes back blank and has to be picked by hand. The review screen exists because of this; don't build a "create without reviewing" shortcut on top of it.
- **No key, no feature.** With `GEMINI_API_KEY` unset the modal returns a plain error instead of hiding itself, so people will find the entry point before they find out it isn't configured. Everything else in the app keeps working.
- **One synchronous call, no retry.** The request blocks for up to 30 seconds with no streaming and no progress indicator, and a timeout or a transient API error surfaces as an error message the person has to resubmit from. Long notes producing many drafts are the slow case.
- **No spend controls.** There's no rate limit, no per-user quota, and no cap on prompt length beyond the browser's — a pasted wall of text is sent as-is. Set budget alerts on the Google AI Studio key rather than relying on the app to restrain itself.
- **The default model id is unverified.** `GEMINI_MODEL` defaults to `gemini-3.1-flash-lite`. Model names get revised and retired; confirm it still exists at ai.google.dev before deploying, or the feature fails at the first call.

### Data and behaviour

- **Board updates via polling, not push.** The board and sidebar refresh every 5-6 seconds, the team panel every 10. There's a few seconds of lag between someone else moving a task and it showing up for you — not instant like the JS version's websocket-based Realtime. Fine for a small team's daily use; worth revisiting if that lag becomes a real complaint.
- **Drag and drop is pointer-only.** It's built on the HTML5 drag events, so there's no keyboard equivalent and it does nothing useful on touch. That's why the Status field stays in the edit modal — it, not the drag, is the accessible path, and removing it would make the board unusable without a mouse.
- **A dropped card moves before the server confirms it.** The card is repositioned immediately and corrected from the response, so for the length of one round trip the board shows the move as done. A refusal or a network error pulls a fresh board straight away, but a drop that fails will visibly undo itself rather than never appearing to happen.
- **Unarchiving a workstream is not an exact undo.** Archiving a workstream archives its projects and tasks as one unit, and unarchiving reverses that across *all* of them. Anything that was archived individually *before* the workstream was archived comes back out too. Rare, but surprising when it happens.
- **`docs/llm-feature-proposal.md` describes a feature this version doesn't have.** It argues for the JS version's Workstream Pulse digest; the LLM feature actually built here is "New task from text". Read it as background on the decision, not as a description of the app.
- **No comments/notifications** — same scope cut as the JS version, contained follow-ups rather than schema changes.

Flag any of these in a review round if they turn out to matter more than expected. Most are contained changes rather than rewrites — the exception is the Gemini data-sharing point, which is a decision to make rather than a bug to fix.
