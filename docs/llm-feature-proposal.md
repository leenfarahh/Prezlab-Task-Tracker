# LLM feature proposal: Workstream Pulse

## Recommendation

Add a single LLM-generated feature: **a short, risk-focused digest across all active workstreams**, generated on demand (and cacheable on a schedule), that leads with the single biggest risk on the board rather than restating what a scan of the kanban view already shows.

This is implemented in the prototype as the "Workstream Pulse" panel and the `/api/pulse` route.

## Why this earns its place, specifically

The brief states the problem plainly: *"Visibility is the problem we are solving, not just record keeping."* A kanban board already solves visibility for a single workstream: color, columns, and the health strip make status obvious at a glance. Where a human scan genuinely fails is **cross-workstream pattern-spotting**: noticing that the same person is quietly overloaded with at-risk tasks across three unrelated workstreams, or that a "blocked" task has sat untouched for eight days while three newer tasks got attention. That kind of pattern lives across rows and across tables; it is exactly the shape of thing a language model reading structured task data is good at surfacing, and exactly the shape of thing a person skimming a board is bad at catching, because it requires holding the whole board in mind at once, every time.

The design choice that makes this earn its place rather than be noise:
- It is **not** a summary of everything ("3 tasks in progress, 2 done..."). That information already exists visually on the board and restating it would be redundant, and redundant AI output erodes trust in the feature over time.
- It **leads with the single biggest risk**, and says plainly when there isn't one, rather than inventing concern to sound useful. A digest that cries wolf trains people to stop reading it.
- Output is capped at 120 words and stored in `pulse_digests`, so it's cheap to generate, cheap to re-read, and auditable (we keep every past digest, so "what did it tell us three weeks ago" is answerable).

## Alternatives considered and rejected

- **Natural-language search / chat over tasks ("ask the board a question").** Genuinely useful eventually, but it answers questions people already know to ask. It doesn't address the stated problem: it's a better retrieval tool, not a visibility tool. Also meaningfully more engineering and eval surface area for a v1.
- **LLM auto-assignment of tasks to owners.** High risk, low trust: an AI silently deciding who owns what work is the kind of decision that needs a human anyway, and a wrong auto-assignment is worse than no assignment. Rejected for v1.
- **LLM-drafted task descriptions from a short prompt.** Convenient, but it's a writing-assistance feature, not a visibility feature, and doesn't address what the brief says is actually broken.

Pulse is the one recommendation because it is the only candidate that directly targets "visibility across workstreams" rather than a different, adjacent problem.

## How it's built (for reference)

- Server-side only: the Next.js route `/api/pulse` reads open (non-`done`) tasks via the Supabase service role, builds a compact prompt (task, status, priority, owner, due date, overdue flag), and calls the Anthropic API with a system prompt that enforces the constraints above (word cap, lead-with-risk, no invented concern, plain sentences).
- The model returns flagged task IDs alongside the narrative; the UI could use these to highlight the specific cards being called out (left as a fast follow, not required for v1 sign-off).
- Every generation is cached in `pulse_digests`, so refreshing the page doesn't call the API again, and the team can look back at what the digest said on any past day.

## How we'll measure whether it's actually useful

A digest feature is easy to ship and easy to let quietly become ignored. The measurement plan is designed to catch that, not just to prove the feature "works" in the sense of returning text.

**Adoption (leading indicator, weeks 1-2)**
- % of active sessions where the Pulse panel is opened/scrolled into view.
- Refresh rate: how often people manually regenerate vs. just read the cached version. A digest nobody bothers to refresh is a digest nobody trusts enough to want current.

**Accuracy and trust (the real test, ongoing)**
- Manual spot-check each review round: pull 10 digests, have a team lead mark each flagged risk as *real*, *already known*, or *noise*. Track the noise rate over time; rising noise is the leading signal to kill or retune the feature before people start ignoring it.
- A lightweight in-app reaction (👍/👎 on each digest) to get a running signal without a survey step. Store it alongside the digest for later analysis.

**Outcome (the thing that actually matters, month 1+)**
- Time-to-detection: for tasks that eventually get flagged as `blocked` with no movement for 5+ days, was that raised in a Pulse digest before or after a human noticed it independently in standup/Slack? This is the real test of whether the feature is catching things earlier than the team would anyway.
- Self-reported time saved / status-meeting length, gathered informally in the second review round rather than as a formal survey, given the team size.

**Kill criteria**
If after two review rounds the noise rate stays high (team consistently marks flagged items as "noise") or adoption stays near zero despite the panel being visible by default, the honest conclusion is that this particular framing isn't earning its place, and it should be reworked (e.g., narrower scope, different trigger cadence) or dropped, rather than kept as a checkbox feature.
