"""Shared, growing pool of already-screened lots per county.

Land Leads serves searches INSTANTLY from this table (no live hit to the
rate-limited cadastral) once a county is warm, and every live search banks its
screened parcels here to grow the pool. Backed by the same Supabase REST access
the report cache uses. See backend/sql/lead_inventory.sql for the schema.
"""
import json
import os
import random

import httpx

from app.core.http import pooled_client

_URL = os.environ.get("SUPABASE_URL", "")
_KEY = (os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_SERVICE_KEY", ""))
_TABLE = f"{_URL}/rest/v1/lead_inventory"
_H = {"apikey": _KEY, "Authorization": f"Bearer {_KEY}"}

# Archiving (`archived` column) is an optional migration. If the column isn't
# there, filtering on it makes PostgREST 400 EVERY read, which silently turned
# inventory serving off (0 served, every search went live). Detect that once and
# drop the filter until the column exists.
_ARCHIVED_COL = True

POOL_MIN = 80   # keep screening a county live until its pool is at least this big,
                # so served results have real variety (not the same N every time)


def _inlist(vals) -> str:
    # PostgREST in.(...) — double-quote each value so hyphens/spaces/periods
    # (Miami-Dade, St. Johns, Palm Beach) are handled.
    return "in.(" + ",".join('"' + str(v).replace('"', '') + '"' for v in vals) + ")"


def _filters(counties, land_types, exclude_kills, acres_min, acres_max,
             value_min, value_max, owner_type, out_of_state, road_access,
             mobile_home_zone=None, sold_within_months=None):
    q = [("county", _inlist(counties))]
    if land_types:
        q.append(("land_type", _inlist(land_types)))
    if exclude_kills:
        q.append(("verdict", "eq.PURSUE"))
    if acres_min:
        q.append(("acreage", f"gte.{acres_min}"))
    if acres_max:
        q.append(("acreage", f"lte.{acres_max}"))
    if value_min:
        q.append(("just_value", f"gte.{value_min}"))
    if value_max:
        q.append(("just_value", f"lte.{value_max}"))
    ot = (owner_type or "").lower()
    if ot == "individual":
        q.append(("owner_biz", "is.false"))
    elif ot == "business":
        q.append(("owner_biz", "is.true"))
    if out_of_state:
        q.append(("owner_state", "neq.FL"))
    if road_access:
        q.append(("has_road", "is.true"))
    # Mobile Home zone filter - checks lead_json for mobile home eligibility
    if mobile_home_zone is not None:
        # Assuming lead_json has a field 'mobile_home_zone' that is boolean or text
        # Adjust the field name and value as needed for your data structure
        q.append(("lead_json->>mobile_home_zone", f"eq.{str(mobile_home_zone).lower()}"))
    # Sold within filter - checks lead_json for recent sale date
    if sold_within_months is not None and sold_within_months > 0:
        # Calculate cutoff date (N months ago)
        from datetime import datetime, timedelta
        cutoff_date = (datetime.now() - timedelta(days=30 * sold_within_months)).strftime('%Y-%m-%d')
        # Assuming lead_json has a field 'last_sale_date' in ISO format (YYYY-MM-DD)
        # Adjust the field name as needed for your data structure
        q.append(("lead_json->>last_sale_date", f"gte.{cutoff_date}"))
    # Exclude archived leads (only when the optional `archived` column exists).
    if _ARCHIVED_COL:
        q.append(("archived", "eq.false"))
    return q


def _missing_archived(r) -> bool:
    """True (and remembers it) if PostgREST rejected the `archived` filter
    because the column doesn't exist (Postgres 42703 undefined_column)."""
    global _ARCHIVED_COL
    if r is not None and r.status_code == 400 and "archived" in r.text and "42703" in r.text:
        _ARCHIVED_COL = False
        print("[Inv] lead_inventory.archived missing — serving without the archive filter")
        return True
    return False


def _strip_archived(params):
    return [p for p in params if p[0] != "archived"]


async def _get(params):
    try:
        async with pooled_client(timeout=6.0) as c:
            r = await c.get(_TABLE, params=params, headers=_H)
            if _missing_archived(r):
                r = await c.get(_TABLE, params=_strip_archived(params), headers=_H)
        if r.status_code == 200:
            return r.json(), r
        print(f"[Inv] get HTTP {r.status_code}: {r.text[:160]}")
    except Exception as e:
        print(f"[Inv] get error: {e}")
    return [], None


async def inv_count(counties, land_types, exclude_kills=True,
                    acres_min=None, acres_max=None, value_min=None, value_max=None,
                    owner_type=None, out_of_state=False, road_access=False,
                    mobile_home_zone=None, sold_within_months=None) -> int:
    """How many matching leads are already banked for these counties."""
    if not _URL or not counties:
        return 0
    params = [("select", "parcel_id"), ("limit", "1")] + \
        _filters(counties, land_types, exclude_kills, acres_min, acres_max,
                 value_min, value_max, owner_type, out_of_state, road_access,
                 mobile_home_zone, sold_within_months)
    try:
        async with pooled_client(timeout=6.0) as c:
            hdr = {**_H, "Prefer": "count=exact", "Range": "0-0"}
            r = await c.get(_TABLE, params=params, headers=hdr)
            if _missing_archived(r):
                r = await c.get(_TABLE, params=_strip_archived(params), headers=hdr)
        cr = r.headers.get("content-range", "")   # "0-0/1234"
        if "/" in cr:
            tot = cr.split("/")[-1]
            return int(tot) if tot.isdigit() else 0
    except Exception as e:
        print(f"[Inv] count error: {e}")
    return 0


_SAMPLE_CAP = 500   # superset size to random-sample the served target from


async def inv_query(*, counties, land_types, target, exclude_kills=True,
                    acres_min=None, acres_max=None, value_min=None, value_max=None,
                    owner_type=None, out_of_state=False, road_access=False,
                    mobile_home_zone=None, sold_within_months=None):
    """Serve up to `target` banked leads, RANDOM-sampled for real variety.

    Two-stage so repeated searches return DIFFERENT lots: (1) a random `rand`
    threshold picks a rotating window of up to _SAMPLE_CAP matching rows across
    the pool (so even a huge county rotates, not just its smallest-rand rows),
    then (2) we random.sample the target out of that window in Python — so two
    searches over an 85-lot pool don't both return the same 30.
    """
    if not _URL or not counties:
        return []
    base = [("select", "lead_json")] + _filters(
        counties, land_types, exclude_kills, acres_min, acres_max,
        value_min, value_max, owner_type, out_of_state, road_access,
        mobile_home_zone, sold_within_months)
    r = random.random()
    rows, _ = await _get(base + [("rand", f"gte.{r}"), ("order", "rand.asc"), ("limit", str(_SAMPLE_CAP))])
    if len(rows) < _SAMPLE_CAP:   # wrap below the threshold to top up the window
        more, _ = await _get(base + [("rand", f"lt.{r}"), ("order", "rand.desc"), ("limit", str(_SAMPLE_CAP - len(rows)))])
        rows += more
    parsed = []
    seen = set()
    for row in rows:
        lj = row.get("lead_json")
        if isinstance(lj, str):
            try:
                lj = json.loads(lj)
            except Exception:
                continue
        if not isinstance(lj, dict):
            continue
        key = lj.get("_folio") or lj.get("address")   # distinct parcels (adjacent lots can share a street label)
        if key in seen:
            continue
        seen.add(key)
        parsed.append(lj)
    if len(parsed) > target:
        parsed = random.sample(parsed, target)
    return parsed


async def inv_upsert(rows):
    """Bank screened parcels (upsert on parcel_id)."""
    if not _URL or not rows:
        return
    try:
        async with pooled_client(timeout=10.0) as c:
            await c.post(_TABLE, json=rows, headers={
                **_H, "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal"})
    except Exception as e:
        print(f"[Inv] upsert error: {e}")
