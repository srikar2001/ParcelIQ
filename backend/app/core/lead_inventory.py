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

_URL = os.environ.get("SUPABASE_URL", "")
_KEY = (os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_SERVICE_KEY", ""))
_TABLE = f"{_URL}/rest/v1/lead_inventory"
_H = {"apikey": _KEY, "Authorization": f"Bearer {_KEY}"}

POOL_MIN = 80   # keep screening a county live until its pool is at least this big,
                # so served results have real variety (not the same N every time)


def _inlist(vals) -> str:
    # PostgREST in.(...) — double-quote each value so hyphens/spaces/periods
    # (Miami-Dade, St. Johns, Palm Beach) are handled.
    return "in.(" + ",".join('"' + str(v).replace('"', '') + '"' for v in vals) + ")"


def _filters(counties, land_types, exclude_kills, acres_min, acres_max,
             value_min, value_max, owner_type, out_of_state, road_access):
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
    return q


async def _get(params):
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(_TABLE, params=params, headers=_H)
        if r.status_code == 200:
            return r.json(), r
    except Exception as e:
        print(f"[Inv] get error: {e}")
    return [], None


async def inv_count(counties, land_types, exclude_kills=True) -> int:
    """How many matching leads are already banked for these counties."""
    if not _URL or not counties:
        return 0
    params = [("select", "parcel_id"), ("limit", "1")] + \
        _filters(counties, land_types, exclude_kills, None, None, None, None, None, False, False)
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(_TABLE, params=params,
                            headers={**_H, "Prefer": "count=exact", "Range": "0-0"})
        cr = r.headers.get("content-range", "")   # "0-0/1234"
        if "/" in cr:
            tot = cr.split("/")[-1]
            return int(tot) if tot.isdigit() else 0
    except Exception as e:
        print(f"[Inv] count error: {e}")
    return 0


async def inv_query(*, counties, land_types, target, exclude_kills=True,
                    acres_min=None, acres_max=None, value_min=None, value_max=None,
                    owner_type=None, out_of_state=False, road_access=False):
    """Serve up to `target` banked leads, RANDOM-sampled for variety."""
    if not _URL or not counties:
        return []
    base = [("select", "lead_json")] + _filters(
        counties, land_types, exclude_kills, acres_min, acres_max,
        value_min, value_max, owner_type, out_of_state, road_access)
    r = random.random()
    rows, _ = await _get(base + [("rand", f"gte.{r}"), ("order", "rand.asc"), ("limit", str(target))])
    if len(rows) < target:   # wrap around the random threshold
        more, _ = await _get(base + [("rand", f"lt.{r}"), ("order", "rand.desc"), ("limit", str(target - len(rows)))])
        rows += more
    out = []
    seen = set()
    for row in rows:
        lj = row.get("lead_json")
        if isinstance(lj, str):
            try:
                lj = json.loads(lj)
            except Exception:
                continue
        if isinstance(lj, dict):
            a = lj.get("address")
            if a in seen:
                continue
            seen.add(a)
            out.append(lj)
    return out


async def inv_upsert(rows):
    """Bank screened parcels (upsert on parcel_id)."""
    if not _URL or not rows:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            await c.post(_TABLE, json=rows, headers={
                **_H, "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal"})
    except Exception as e:
        print(f"[Inv] upsert error: {e}")
