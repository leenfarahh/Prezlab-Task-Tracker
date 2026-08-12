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
    create type task_status as enum ('backlog', 'in_progress', 'at_risk', 'blocked', 'in_review', 'done');
  end if;
  if not exists (select 1 from pg_type where typname = 'task_priority') then
    create type task_priority as enum ('low', 'medium', 'high', 'urgent');
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

-- Auto-log status changes so the feed stays reliable without relying on the client to log it.
create or replace function public.log_task_change()
returns trigger as $$
begin
  if (tg_op = 'INSERT') then
    insert into public.task_activity (task_id, actor_id, kind, detail)
    values (new.id, new.created_by, 'created', jsonb_build_object('status', new.status));
  elsif (tg_op = 'UPDATE') then
    if new.status is distinct from old.status then
      insert into public.task_activity (task_id, actor_id, kind, detail)
      values (new.id, new.assignee_id, 'status_changed', jsonb_build_object('from', old.status, 'to', new.status));
    end if;
    if new.assignee_id is distinct from old.assignee_id then
      insert into public.task_activity (task_id, actor_id, kind, detail)
      values (new.id, new.assignee_id, 'reassigned', jsonb_build_object('from', old.assignee_id, 'to', new.assignee_id));
    end if;
    if new.due_date is distinct from old.due_date then
      insert into public.task_activity (task_id, actor_id, kind, detail)
      values (new.id, new.assignee_id, 'due_date_changed', jsonb_build_object('from', old.due_date, 'to', new.due_date));
    end if;
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists tasks_log_change on public.tasks;
create trigger tasks_log_change
  after insert or update on public.tasks
  for each row execute procedure public.log_task_change();

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

-- ---------- Dropped columns from earlier revisions ----------
-- All no-ops on a fresh project. Named on both tables because the swap above
-- means either name could be holding a column an older revision put on the
-- other one.

alter table public.workstreams drop column if exists product_id;
alter table public.projects drop column if exists product_id;
drop table if exists public.products;

-- Workstreams and projects are editable by any allow-listed user, not just a
-- designated owner - only tasks are restricted to their creator/assignee.
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
