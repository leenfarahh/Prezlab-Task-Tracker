-- Prezlab AI Team Tracker: core schema
-- Run this in the Supabase SQL editor (or via `supabase db push`) on a fresh project.
--
-- Hierarchy: Workstream -> Project -> Task/assignee. If this project last ran a
-- revision of this file that nested it the other way round (Project ->
-- Workstream -> Task), the guarded "Invert the hierarchy" block below migrates
-- it in place without touching a row - see the note there.

create extension if not exists "pgcrypto";

-- ---------- Enums ----------
-- Postgres has no `create type if not exists`, so these are guarded manually
-- to keep this file safe to re-run on a project that already ran it once.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'task_status') then
    create type task_status as enum ('backlog', 'in_progress', 'pending', 'blocked', 'in_review', 'done');
  end if;
  if not exists (select 1 from pg_type where typname = 'task_priority') then
    create type task_priority as enum ('low', 'medium', 'high', 'urgent');
  end if;
end $$;

-- The status formerly labelled 'at_risk' is now 'pending'. Renaming the enum
-- label rather than adding a new one and migrating rows: the label is the value,
-- so every task, index entry and default follows it with no row rewritten and no
-- window in which both spellings are valid. Guarded on the old label still being
-- present, so this is a no-op on a fresh project and safe to re-run.
do $$
begin
  if exists (
    select 1
    from pg_enum e
    join pg_type t on t.oid = e.enumtypid
    where t.typname = 'task_status' and e.enumlabel = 'at_risk'
  ) then
    alter type task_status rename value 'at_risk' to 'pending';
  end if;
end $$;

-- ---------- People ----------
-- No Supabase Auth involved: email is the identity, and the app upserts a
-- profiles row directly on login (see app/routers/auth_router.py). id is
-- self-generated instead of mirroring auth.users.

create table if not exists public.profiles (
  id uuid primary key default gen_random_uuid(),
  full_name text not null,
  email text not null,
  avatar_color text not null default '#4C5FD5',
  role text,
  avatar_url text,
  created_at timestamptz not null default now()
);

alter table public.profiles add column if not exists role text;
alter table public.profiles add column if not exists avatar_url text;

-- Read watermark for the notification bell and the activity page: a comment on
-- one of your tasks counts as unread if it is newer than this. Opening
-- /activity pushes it to now().
--
-- `default now()` rather than null on purpose. Null would read as "never
-- checked", so every profile that already exists when this runs would light up
-- with a count of every historical comment on their tasks - a backlog nobody
-- asked for. Defaulting to the migration time starts everyone clean.
alter table public.profiles add column if not exists comments_seen_at timestamptz not null default now();

-- Detach from auth.users and enforce one profile per email, for installs that
-- ran an earlier version of this file (both are no-ops on a fresh project).
alter table public.profiles drop constraint if exists profiles_id_fkey;
alter table public.profiles alter column id set default gen_random_uuid();

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'profiles_email_key') then
    alter table public.profiles add constraint profiles_email_key unique (email);
  end if;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
drop function if exists public.handle_new_user();

-- ---------- Workstreams ----------
-- Top-level grouping: a standing area of work (a client, a product, an internal
-- track) that individual projects are opened and closed inside of.

create table if not exists public.workstreams (
  id uuid primary key default gen_random_uuid(),
  name text not null, -- generic label only, e.g. "Client A" - never a real client name (see confidentiality note in README)
  is_archived boolean not null default false,
  created_at timestamptz not null default now()
);

-- ---------- Projects ----------
-- A single piece of delivery inside a workstream, and the level tasks attach to.

create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  workstream_id uuid not null references public.workstreams (id),
  color text not null default '#4C5FD5',
  is_archived boolean not null default false,
  created_at timestamptz not null default now()
);

-- Who created it. Added because renaming, archiving and especially DELETING a
-- project or workstream were open to any signed-in user - and delete is
-- irreversible and cascades to every task and comment underneath. There was no
-- column to check against, so the guard could not exist at all.
--
-- Nullable, and null means "created before this column existed". Those rows
-- have no creator to protect them, so the app treats them as unowned and lets
-- anyone act on them; anything created from now on is creator-only. Not
-- backfilled to a guessed owner, for the same reason the activity feed says
-- "was created" rather than naming someone: the information was never recorded.
alter table public.projects add column if not exists created_by uuid references public.profiles (id);
alter table public.workstreams add column if not exists created_by uuid references public.profiles (id);

-- ---------- Tasks ----------

create table if not exists public.tasks (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects (id) on delete cascade,
  title text not null,
  description text,
  status task_status not null default 'backlog',
  priority task_priority not null default 'medium',
  assignee_id uuid references public.profiles (id),
  due_date date,
  created_by uuid references public.profiles (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.tasks add column if not exists is_archived boolean not null default false;

-- ---------- Invert the hierarchy: Project -> Workstream becomes Workstream -> Project ----------
-- Only fires on a project that ran an earlier revision of this file, where
-- workstreams sat *under* projects and tasks hung off workstreams. Guarded on
-- that old parent column still existing, so it is a no-op on a fresh project
-- and safe to re-run.
--
-- A foreign key follows the physical table through a rename, so swapping the
-- two table names and then the two child columns inverts the hierarchy without
-- rewriting a single row: what used to be a top-level project is now a
-- workstream, each workstream it contained is now one of that workstream's
-- projects, and every task stays attached to the same node it was already on.
--
-- Installs predating the project/workstream split entirely (a `client_label`
-- column on workstreams and no projects table) are no longer handled here -
-- run the previous revision of this file first, then this one.

do $$
begin
  if exists (
    select 1 from pg_attribute
    where attrelid = to_regclass('public.workstreams')
      and attname = 'project_id'
      and not attisdropped
  ) then
    alter table public.projects rename to hierarchy_swap_tmp;
    alter table public.workstreams rename to projects;
    alter table public.hierarchy_swap_tmp rename to workstreams;

    alter table public.projects rename column project_id to workstream_id;
    alter table public.tasks rename column workstream_id to project_id;

    -- Indexes and constraints followed their tables through the rename, so their
    -- names now describe the wrong level. Nothing depends on those names, but
    -- leaving them would both mislead anyone reading `\d` and let the
    -- `create index if not exists` statements below add a second index over a
    -- column that is already indexed under the old name.
    alter index if exists public.projects_is_archived_idx rename to workstreams_is_archived_idx;
    alter index if exists public.workstreams_project_idx rename to projects_workstream_idx;
    alter index if exists public.tasks_workstream_idx rename to tasks_project_idx;

    -- Renaming an index renames the constraint it backs, so the two primary
    -- keys have to pass through a free name rather than swapping directly.
    alter index if exists public.projects_pkey rename to hierarchy_swap_pkey;
    alter index if exists public.workstreams_pkey rename to projects_pkey;
    alter index if exists public.hierarchy_swap_pkey rename to workstreams_pkey;

    if exists (select 1 from pg_constraint where conname = 'workstreams_project_id_fkey') then
      alter table public.projects rename constraint workstreams_project_id_fkey to projects_workstream_id_fkey;
    end if;
    if exists (select 1 from pg_constraint where conname = 'tasks_workstream_id_fkey') then
      alter table public.tasks rename constraint tasks_workstream_id_fkey to tasks_project_id_fkey;
    end if;
  end if;
end $$;

-- ---------- Indexes ----------

create index if not exists workstreams_is_archived_idx on public.workstreams (is_archived);
create index if not exists projects_workstream_idx on public.projects (workstream_id);
create index if not exists tasks_project_idx on public.tasks (project_id);
create index if not exists tasks_status_idx on public.tasks (status);
create index if not exists tasks_assignee_idx on public.tasks (assignee_id);
create index if not exists tasks_is_archived_idx on public.tasks (is_archived);

create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists tasks_set_updated_at on public.tasks;
create trigger tasks_set_updated_at
  before update on public.tasks
  for each row execute procedure public.set_updated_at();

-- ---------- Activity log ----------
-- Append-only trail that the UI feed reads from.

create table if not exists public.task_activity (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references public.tasks (id) on delete cascade,
  actor_id uuid references public.profiles (id),
  kind text not null, -- 'created' | 'status_changed' | 'reassigned' | 'commented' | 'due_date_changed'
  detail jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create index if not exists task_activity_task_idx on public.task_activity (task_id);

-- Snapshots taken at log time. A "deleted" event has to outlive the task it
-- describes, and once the task row is gone there is nothing left to join to for
-- a title - so the readable parts are copied onto the event itself.
alter table public.task_activity add column if not exists task_title text;
alter table public.task_activity add column if not exists project_name text;

-- Deliberately a bare uuid with no foreign key. It is a snapshot for the
-- activity page's project filter, and a reference would either take the row
-- down with the project or null the very value being filtered on - both of
-- which lose the history this table exists to keep.
alter table public.task_activity add column if not exists project_id uuid;
create index if not exists task_activity_project_idx on public.task_activity (project_id);

-- The feed covers workstreams and projects as well as tasks, so what an event
-- happened to is now named explicitly. The table keeps its task-shaped columns
-- (and its name) because rows written before this still use them, and because
-- task_id is what the feed needs to open a task modal; entity_* is the shared
-- pair everything reads back through. Bare uuid again, for the same reason as
-- project_id above - the row must outlive what it describes.
alter table public.task_activity add column if not exists entity_type text not null default 'task';
alter table public.task_activity add column if not exists entity_id uuid;
alter table public.task_activity add column if not exists entity_title text;
create index if not exists task_activity_entity_idx on public.task_activity (entity_type, entity_id);

-- Carry existing task rows onto the shared columns. Guarded on `is null`, so it
-- only fills what is missing and is safe to re-run.
update public.task_activity
set entity_id = task_id
where entity_id is null and task_id is not null;

update public.task_activity
set entity_title = task_title
where entity_title is null and task_title is not null;

-- Who this event belongs to: the task's creator, its assignee, and whoever
-- acted, as of the moment it happened. The activity feed filters on this rather
-- than joining back to tasks, which is what lets it keep showing events for
-- tasks that no longer exist, and stops a later reassignment rewriting whose
-- history an old event appears in.
alter table public.task_activity add column if not exists audience uuid[] not null default '{}';
create index if not exists task_activity_audience_idx on public.task_activity using gin (audience);
create index if not exists task_activity_created_idx on public.task_activity (created_at desc);

-- task_id was `not null ... on delete cascade`, which meant deleting a task also
-- deleted the record that it had been deleted. Now it goes null and the event
-- survives, carrying the snapshot above.
alter table public.task_activity drop constraint if exists task_activity_task_id_fkey;
alter table public.task_activity alter column task_id drop not null;
alter table public.task_activity
  add constraint task_activity_task_id_fkey
  foreign key (task_id) references public.tasks (id) on delete set null;

-- The trigger that used to write this table is gone, deliberately. It could not
-- know who acted: this app talks to Postgres with a single service-role key and
-- no per-request database user, so the trigger logged new.assignee_id as the
-- actor - the person the task belongs to, not the person who touched it.
-- Reassigning someone else's task recorded *them* as having done it. The
-- application knows the real actor from the session and logs it there instead
-- (app/activity_log.py), which is also the only place that can distinguish an
-- edit from an archive from a delete.
drop trigger if exists tasks_log_change on public.tasks;
drop function if exists public.log_task_change();

-- Status changes keep the status as plain text inside detail, so the enum rename
-- above ('at_risk' -> 'pending') does not reach them. Without this, every
-- historical "moved to At risk" event would render the raw value it no longer has
-- a label for. The event itself is unchanged - the status it names is the same
-- status, under the name it now has everywhere else. Idempotent: after this runs
-- there is nothing left matching the old value.
update public.task_activity
set detail = detail || jsonb_build_object('from', 'pending')
where detail->>'from' = 'at_risk';

update public.task_activity
set detail = detail || jsonb_build_object('to', 'pending')
where detail->>'to' = 'at_risk';

-- ---------- Comments ----------
-- Discussion on a task, open to the whole team. Deliberately NOT restricted to
-- the task's creator or assignee the way editing is (see _task_owner_denial in
-- app/routers/tasks_router.py): anyone should be able to ask a question or
-- leave context on someone else's task without being handed it first. Only the
-- comment's own author can delete it.
--
-- Kept as its own table rather than a task_activity row with kind='commented':
-- a comment is authored content with its own lifecycle, while task_activity is
-- an append-only trail of system events written by a trigger. Mixing them would
-- put user text under a jsonb detail column and make "delete my comment" a
-- write into an audit log.

create table if not exists public.task_comments (
  id uuid primary key default gen_random_uuid(),
  -- Tasks are hard-deleted (not just archived), so comments follow the task out.
  task_id uuid not null references public.tasks (id) on delete cascade,
  author_id uuid references public.profiles (id),
  body text not null,
  created_at timestamptz not null default now()
);

-- Composite because every read is "this task's comments, oldest first".
create index if not exists task_comments_task_idx on public.task_comments (task_id, created_at);

-- ---------- Backfill: history that predates the activity feed ----------
-- Comments and tasks created before this file was last run have no events, so
-- the feed would start empty and appear to have lost them. Both inserts below
-- are idempotent and safe to re-run.

-- Ties an event to the comment it was generated from, so the backfill can tell
-- what it has already written. Unique, and Postgres allows many nulls in a
-- unique index, so rows from the other event kinds are unaffected.
alter table public.task_activity add column if not exists source_comment_id uuid;
create unique index if not exists task_activity_source_comment_idx
  on public.task_activity (source_comment_id);

insert into public.task_activity
  (task_id, actor_id, kind, task_title, project_name, project_id, audience, detail, created_at, source_comment_id)
select
  c.task_id,
  c.author_id,
  'commented',
  t.title,
  p.name,
  t.project_id,
  -- Same audience the application computes: creator, assignee, actor. coalesce
  -- because the column is `not null` and all three can be null on old rows.
  coalesce((
    select array_agg(distinct x)
    from unnest(array[t.created_by, t.assignee_id, c.author_id]) as x
    where x is not null
  ), '{}'),
  jsonb_build_object('excerpt', left(c.body, 180)),
  c.created_at,  -- the comment's own time, not now(), so the feed reads in order
  c.id
from public.task_comments c
join public.tasks t on t.id = c.task_id
left join public.projects p on p.id = t.project_id
on conflict (source_comment_id) do nothing;

-- Task creations are reconstructable from created_by/created_at, which is real
-- recorded data. No marker column needed: the application writes exactly one
-- 'created' event per task, so "has one already" is the idempotency check.
-- Project and workstream creations follow further down, on the same principle;
-- the note at the end of this section covers what cannot be recovered at all.
insert into public.task_activity
  (task_id, actor_id, kind, task_title, project_name, project_id, audience, detail, created_at)
select
  t.id,
  t.created_by,
  'created',
  t.title,
  p.name,
  t.project_id,
  coalesce((
    select array_agg(distinct x)
    from unnest(array[t.created_by, t.assignee_id]) as x
    where x is not null
  ), '{}'),
  '{}'::jsonb,
  t.created_at
from public.tasks t
left join public.projects p on p.id = t.project_id
where not exists (
  select 1 from public.task_activity a
  where a.kind = 'created' and a.task_id = t.id
);

-- Fills project_id on rows written before that column existed - both earlier
-- backfill runs and anything the app logged in between. Guarded on `is null`,
-- so it only ever touches rows that are actually missing it and is safe to
-- re-run. Rows whose task has since been deleted keep a null project_id and
-- simply won't match the project filter; their project is genuinely unknown.
update public.task_activity a
set project_id = t.project_id
from public.tasks t
where a.project_id is null
  and a.task_id = t.id;

-- Project and workstream creations. created_at is real, and created_by is used
-- as the actor wherever it exists - which is anything made since that column was
-- added. Rows older than it have no recorded creator, so those stay null and the
-- feed renders them passively ("project X was created") rather than naming
-- someone. Audience is whoever has a task under it, so these land in the feed of
-- the people the thing actually concerns.
--
-- Idempotency: one 'created' event per entity, same check the task creations
-- above use.
insert into public.task_activity
  (actor_id, kind, entity_type, entity_id, entity_title, project_id, audience, detail, created_at)
select
  pr.created_by,
  'created',
  'project',
  pr.id,
  pr.name,
  pr.id,
  coalesce((
    select array_agg(distinct x)
    from public.tasks t, unnest(array[t.created_by, t.assignee_id]) as x
    where t.project_id = pr.id and x is not null
  ), '{}'),
  '{}'::jsonb,
  pr.created_at
from public.projects pr
where not exists (
  select 1 from public.task_activity a
  where a.kind = 'created' and a.entity_type = 'project' and a.entity_id = pr.id
);

insert into public.task_activity
  (actor_id, kind, entity_type, entity_id, entity_title, audience, detail, created_at)
select
  w.created_by,
  'created',
  'workstream',
  w.id,
  w.name,
  coalesce((
    select array_agg(distinct x)
    from public.projects pr
    join public.tasks t on t.project_id = pr.id,
    unnest(array[t.created_by, t.assignee_id]) as x
    where pr.workstream_id = w.id and x is not null
  ), '{}'),
  '{}'::jsonb,
  w.created_at
from public.workstreams w
where not exists (
  select 1 from public.task_activity a
  where a.kind = 'created' and a.entity_type = 'workstream' and a.entity_id = w.id
);

-- Attributes creation events that were written before created_by existed, or
-- before the app started recording it. Guarded on `actor_id is null`, so it
-- only ever fills a gap and is safe to re-run. Rows whose entity still has no
-- recorded creator stay null - there is nothing to copy.
update public.task_activity a
set actor_id = pr.created_by
from public.projects pr
where a.actor_id is null and a.kind = 'created'
  and a.entity_type = 'project' and a.entity_id = pr.id
  and pr.created_by is not null;

update public.task_activity a
set actor_id = w.created_by
from public.workstreams w
where a.actor_id is null and a.kind = 'created'
  and a.entity_type = 'workstream' and a.entity_id = w.id
  and w.created_by is not null;

update public.task_activity a
set actor_id = t.created_by
from public.tasks t
where a.actor_id is null and a.kind = 'created'
  and a.entity_type = 'task' and a.entity_id = t.id
  and t.created_by is not null;

-- Nothing else can be backfilled, and the reason is worth stating plainly
-- rather than leaving as a gap someone later tries to "fix":
--
--   * Archives. is_archived is a boolean with no timestamp and no actor. Tasks
--     have updated_at, but that is the time of the LAST change of any kind, so
--     using it would date an archive to whenever the task was last edited.
--     Projects and workstreams have no updated_at at all.
--   * Edits, status changes and reassignments. Only the current value is
--     stored; previous values were never kept anywhere.
--   * Deletions. The rows are gone. Nothing records that they existed.
--
-- Inventing timestamps or actors for these would put wrong history in front of
-- people who would reasonably trust it.

-- ---------- Session refresh tokens ----------
-- The signed cookie set on login (app/main.py) is a short-lived, in-memory
-- session. This table backs a longer-lived refresh token in a second cookie,
-- so a session survives the signed cookie expiring without re-entering an
-- email, and so a specific session can be force-ended server-side (logout,
-- or revoking a departed teammate's active sessions) by setting revoked_at.
-- Only ever queried with the service-role key - see app/tokens.py.

create table if not exists public.auth_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists auth_tokens_user_idx on public.auth_tokens (user_id);

alter table public.auth_tokens enable row level security;
drop policy if exists "auth_tokens no client access" on public.auth_tokens;
create policy "auth_tokens no client access" on public.auth_tokens
  for all using (false) with check (false);

-- ---------- Daily digests ----------
-- One generated morning digest per person per day (app/digest.py). Cached in a
-- table rather than in process memory so it survives a restart and is shared
-- across workers - the point of "once a day" is that the same person doesn't
-- pay for a second generation, which an in-memory cache can't promise.
--
-- Private to its subject by construction: every read is keyed by the session's
-- own user_id, and nothing joins this table into a shared view.
--
-- Rows are replaced, not appended: (user_id, digest_date) is unique and a manual
-- refresh upserts over the day's row. Yesterday's digest is kept because it
-- costs nothing, but nothing reads it - the digest is about today.

create table if not exists public.daily_digests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  digest_date date not null,
  headline text not null default '',
  summary text not null,
  focus text[] not null default '{}',
  -- What the tasks looked like when this was written. A digest that no longer
  -- matches the board is worse than no digest, so the page compares this against
  -- the current tasks and flags a stale one rather than quietly showing it.
  task_fingerprint text not null default '',
  created_at timestamptz not null default now(),
  unique (user_id, digest_date)
);

create index if not exists daily_digests_user_date_idx on public.daily_digests (user_id, digest_date desc);

alter table public.daily_digests enable row level security;
drop policy if exists "daily_digests no client access" on public.daily_digests;
create policy "daily_digests no client access" on public.daily_digests
  for all using (false) with check (false);

-- ---------- Dropped columns from earlier revisions ----------
-- All no-ops on a fresh project. Named on both tables because the swap above
-- means either name could be holding a column an older revision put on the
-- other one.

alter table public.workstreams drop column if exists product_id;
alter table public.projects drop column if exists product_id;
drop table if exists public.products;

-- owner_id is gone for good; created_by below replaces it. The distinction is
-- deliberate: owner_id was an assignable role, created_by is a fact about who
-- made the thing, and only the second one can be recorded without someone
-- having to maintain it.
alter table public.workstreams drop column if exists owner_id;
alter table public.projects drop column if exists owner_id;

-- ---------- Row Level Security ----------
-- Internal team tool: any authenticated team member can read/write everything.
-- This is intentionally simple (no per-workstream ACLs yet) - see README for how
-- to tighten this if the tool grows past a single internal team.
--
-- Note: since login no longer goes through Supabase Auth (see People, above),
-- auth.role() here never actually evaluates to 'authenticated' for any request -
-- these policies are effectively "deny all" for the anon key, which is the safe
-- default. All real access happens through app/supabase_client.py's service-role
-- client, which bypasses RLS entirely; enforcement lives in FastAPI's
-- require_login, not here.

alter table public.profiles enable row level security;
alter table public.workstreams enable row level security;
alter table public.projects enable row level security;
alter table public.tasks enable row level security;
alter table public.task_activity enable row level security;
alter table public.task_comments enable row level security;

drop policy if exists "profiles readable by authenticated users" on public.profiles;
create policy "profiles readable by authenticated users" on public.profiles
  for select using (auth.role() = 'authenticated');
drop policy if exists "profiles updatable by owner" on public.profiles;
create policy "profiles updatable by owner" on public.profiles
  for update using (auth.uid() = id);

-- A policy's name travelled with its table through the swap above, so each of
-- these two tables can be holding the other's policy - drop both names on both
-- before recreating, or the stale one lingers alongside the new one.
drop policy if exists "workstreams full access for authenticated users" on public.workstreams;
drop policy if exists "workstreams full access for authenticated users" on public.projects;
drop policy if exists "projects full access for authenticated users" on public.workstreams;
drop policy if exists "projects full access for authenticated users" on public.projects;

create policy "workstreams full access for authenticated users" on public.workstreams
  for all using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
create policy "projects full access for authenticated users" on public.projects
  for all using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

drop policy if exists "tasks full access for authenticated users" on public.tasks;
create policy "tasks full access for authenticated users" on public.tasks
  for all using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

drop policy if exists "activity readable by authenticated users" on public.task_activity;
create policy "activity readable by authenticated users" on public.task_activity
  for select using (auth.role() = 'authenticated');
drop policy if exists "activity insertable by authenticated users" on public.task_activity;
create policy "activity insertable by authenticated users" on public.task_activity
  for insert with check (auth.role() = 'authenticated');

drop policy if exists "comments full access for authenticated users" on public.task_comments;
create policy "comments full access for authenticated users" on public.task_comments
  for all using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

-- ---------- Realtime ----------
-- Enables the dashboard to update live as tasks change status, which is the
-- mechanism behind "visibility at a glance" rather than a manual refresh.
-- Guarded because `alter publication ... add table` errors if the table is
-- already a member, unlike the `if not exists` forms used elsewhere here.

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'tasks'
  ) then
    alter publication supabase_realtime add table public.tasks;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'task_activity'
  ) then
    alter publication supabase_realtime add table public.task_activity;
  end if;
end $$;

-- ---------- Avatar storage ----------
-- Public bucket for profile photos (see app/routers/user_router.py). Public
-- so uploaded photos render via a plain <img src> without signed URLs - all
-- writes still go through the service-role key, same as every other table.

insert into storage.buckets (id, name, public)
values ('avatars', 'avatars', true)
on conflict (id) do nothing;
