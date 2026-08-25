-- ParcelIQ · Deal Pipeline
-- Run this once in the Supabase SQL editor (Dashboard → SQL Editor → New query → paste → Run).
-- Safe to run more than once (idempotent).

create table if not exists public.deals (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,

  -- parcel identity + snapshot (so a card renders without reloading its batch)
  address         text,
  apn             text,
  county          text,
  acreage         numeric,
  verdict         text,
  score           integer,
  lat             double precision,
  lng             double precision,
  parcel_info     jsonb,
  source_batch_id text,

  -- deal workflow
  stage           text not null default 'New',
  offer_amount    numeric,
  owner_name      text,
  owner_phone     text,
  owner_email     text,
  owner_mailing   text,
  next_action_at  date,
  tags            text[]  default '{}',
  notes           jsonb   default '[]'::jsonb,
  sort_order      double precision default 0,

  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists deals_user_stage_idx on public.deals (user_id, stage, sort_order);

-- Row-level security: every user sees and edits ONLY their own deals.
alter table public.deals enable row level security;

drop policy if exists deals_select_own on public.deals;
create policy deals_select_own on public.deals
  for select using (auth.uid() = user_id);

drop policy if exists deals_insert_own on public.deals;
create policy deals_insert_own on public.deals
  for insert with check (auth.uid() = user_id);

drop policy if exists deals_update_own on public.deals;
create policy deals_update_own on public.deals
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists deals_delete_own on public.deals;
create policy deals_delete_own on public.deals
  for delete using (auth.uid() = user_id);

-- Keep updated_at fresh on every edit.
create or replace function public.deals_touch_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists deals_touch on public.deals;
create trigger deals_touch before update on public.deals
  for each row execute function public.deals_touch_updated_at();
