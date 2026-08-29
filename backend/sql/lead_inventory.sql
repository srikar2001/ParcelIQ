-- ParcelIQ · Land Leads inventory
-- A shared, growing pool of already-screened lots per county. Land Leads serves
-- searches INSTANTLY from this table (no live hit to the rate-limited cadastral),
-- and every live search banks its screened parcels here to grow the pool.
-- Run once in the Supabase SQL editor. Safe to run more than once.

create table if not exists public.lead_inventory (
  parcel_id     text primary key,
  county        text,
  land_type     text,                 -- 'Vacant Land' | 'Agricultural' | ...
  verdict       text,                 -- PURSUE | REVIEW | KILL
  score         integer,
  acreage       numeric,
  just_value    numeric,
  owner_biz     boolean,              -- owner looks like a business/LLC
  owner_state   text,                 -- for the out-of-state filter
  has_road      boolean,              -- road access confirmed
  address       text,
  lat           double precision,
  lng           double precision,
  lead_json     jsonb,                -- the full lead row, ready to return as-is
  rand          double precision default random(),   -- random sampling key (variety)
  screened_at   timestamptz default now()
);

-- Fast filtered serve + random sampling.
create index if not exists inv_serve_idx  on public.lead_inventory (county, land_type, verdict, rand);
create index if not exists inv_acre_idx    on public.lead_inventory (acreage);
create index if not exists inv_value_idx   on public.lead_inventory (just_value);

-- Shared derived cache (like the reports cache) — not per-user data. Keep RLS
-- off and grant the API roles access so the backend can read/write it.
alter table public.lead_inventory disable row level security;
grant select, insert, update on public.lead_inventory to anon, authenticated, service_role;
