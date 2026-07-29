"""Land Leads — vacant-land prospecting.

The statewide cadastral only answers small spatial queries quickly (county-wide
attribute scans time out), so we work from geographic CENTERS: for each selected
county (or a typed area) we geocode a center once (free, cached), pull the
parcels in a small bbox around it (fast spatial query), keep the vacant ones
matching the filters, and screen those by centroid — never geocoding per parcel,
so zero paid credits. Each screen is cached per parcel_id, screening is bounded
by a hard deadline + concurrency cap (so a search ALWAYS returns), and results
come back in batch-result shape (frontend reuses the results table + Deal Review).
"""
import asyncio
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.collectors.parcel_fl import URL as _CAD_URL, _CO_NO_TO_COUNTY
from app.routers.batch import _screen_coordinate, _user_id_from_token, _AuthUnavailable
from app.core.cache import get_cached_result, save_cached_result

router = APIRouter(prefix="/api/leads")

# Bounded concurrency for lead screening (a search only ever screens _MAX_SCREEN
# parcels, so a modest bump over the batch path is safe for the DB).
_SEM_LEADS = asyncio.Semaphore(6)
_LEAD_CONCURRENCY = 6

_VACANT_CODES = {0, 9, 10, 40, 70}
_BBOX_HALF = 0.03                # ±0.03deg ≈ 6.5km box per center — stays fast
_MAX_SCREEN = 20                 # hard cap on fresh screens per search
_TARGET_LEADS = 25
_SCREEN_TIMEOUT = 22.0           # drop a parcel that's slow to screen
_DEADLINE_S = 42.0               # overall wall-clock budget — always return by here
_BIZ_MARKERS = ("LLC", "L.L.C", "INC", "CORP", "LTD", "TRUST", "PROPERTIES",
                "HOLDINGS", "INVESTMENT", "CAPITAL", "GROUP", "ENTERPRISE",
                "COMPANY", "PARTNERS", "REALTY", "HOMES", "DEVELOPMENT",
                "VENTURES", "ASSOCIATES", "FUND", "BANK", " LP")

_county_center_cache: dict = {}


class LeadFilters(BaseModel):
    counties: Optional[list] = None        # list of FL county names
    location: Optional[str] = None         # optional free-text area override
    acres_min: Optional[float] = None
    acres_max: Optional[float] = None
    value_min: Optional[float] = None
    value_max: Optional[float] = None
    individual_only: bool = False
    out_of_state: bool = False
    road_access: bool = False
    exclude_kills: bool = True


async def _require_auth(authorization: Optional[str]) -> Optional[JSONResponse]:
    token = (authorization or "").removeprefix("Bearer ").strip()
    try:
        ident = await _user_id_from_token(token)
    except _AuthUnavailable:
        return None
    if not ident:
        return JSONResponse(status_code=401, content={"error": "Sign in to generate leads."})
    return None


def _is_business(owner: Optional[str]) -> bool:
    o = (owner or "").upper()
    return any(m in o for m in _BIZ_MARKERS)


def _ring_center(ring):
    if not ring:
        return None
    sx = sy = 0.0
    n = 0
    for pt in ring:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            sx += pt[0]; sy += pt[1]; n += 1
    return (sy / n, sx / n) if n else None


async def _geocode(q: str):
    if not q:
        return None
    key = q.strip().lower()
    if key in _county_center_cache:
        return _county_center_cache[key]
    query = q if ("fl" in key or "florida" in key) else (q + ", Florida, USA")
    try:
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": "ParcelIQ-Leads/1.0"}) as client:
            r = await client.get("https://nominatim.openstreetmap.org/search",
                                  params={"q": query, "format": "json", "limit": "1", "countrycodes": "us"})
            arr = r.json()
        if arr:
            center = (float(arr[0]["lat"]), float(arr[0]["lon"]))
            _county_center_cache[key] = center
            return center
    except Exception as ex:
        print(f"[Leads] geocode failed for {q!r}: {ex}")
    return None


@router.get("/counties")
async def counties():
    return {"counties": sorted(_CO_NO_TO_COUNTY.values())}


async def _fetch_bbox_parcels(lat: float, lng: float) -> list:
    w, s, e, n = lng - _BBOX_HALF, lat - _BBOX_HALF, lng + _BBOX_HALF, lat + _BBOX_HALF
    params = {
        "where": "1=1",
        "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "outSR": "4326", "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PARCEL_ID,OWN_NAME,OWN_STATE,PHY_ADDR1,DOR_UC,JV,LND_SQFOOT",
        "returnGeometry": "true", "resultRecordCount": "500", "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(_CAD_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as ex:
        print(f"[Leads] bbox fetch error @{lat},{lng}: {ex}")
        return []
    if "error" in data:
        return []
    out = []
    for feat in data.get("features", []):
        a = feat.get("attributes", {})
        try:
            luc = int(a.get("DOR_UC"))
        except (TypeError, ValueError):
            continue
        if luc not in _VACANT_CODES:
            continue
        rings = (feat.get("geometry") or {}).get("rings") or []
        center = _ring_center(rings[0]) if rings else None
        if not center:
            continue
        sq = a.get("LND_SQFOOT")
        out.append({
            "parcel_id": a.get("PARCEL_ID"),
            "lat": center[0], "lng": center[1],
            "address": (a.get("PHY_ADDR1") or "").strip() or None,
            "owner": (a.get("OWN_NAME") or "").strip() or None,
            "owner_state": (a.get("OWN_STATE") or "").strip().upper() or None,
            "acreage": round(sq / 43560, 4) if sq else None,
            "just_value": a.get("JV"),
        })
    return out


def _match(c: dict, f: LeadFilters) -> bool:
    ac = c.get("acreage"); jv = c.get("just_value")
    if f.acres_min is not None and (ac is None or ac < f.acres_min):
        return False
    if f.acres_max is not None and (ac is None or ac > f.acres_max):
        return False
    if f.value_min is not None and (jv is None or jv < f.value_min):
        return False
    if f.value_max is not None and (jv is None or jv > f.value_max):
        return False
    if jv is not None and jv <= 100:
        return False
    if f.individual_only and _is_business(c.get("owner")):
        return False
    if f.out_of_state and (c.get("owner_state") in (None, "FL", "")):
        return False
    return True


async def _screen_candidate(cand: dict, budget: dict) -> Optional[dict]:
    pid = cand.get("parcel_id")
    cache_key = f"lead:{pid}" if pid else None
    if cache_key:
        cached = await get_cached_result(cache_key)
        if cached is not None:
            return cached
    if budget["n"] <= 0:
        return None
    budget["n"] -= 1
    async with _SEM_LEADS:
        try:
            res = await asyncio.wait_for(
                _screen_coordinate(cand.get("address") or "", {"lat": cand["lat"], "lng": cand["lng"]}, cand["lat"], cand["lng"]),
                timeout=_SCREEN_TIMEOUT,
            )
        except Exception as ex:
            print(f"[Leads] screen error {pid}: {ex}")
            return None
    res["_lat"] = cand["lat"]; res["_lng"] = cand["lng"]
    if not res.get("address"):
        res["address"] = cand.get("address") or (str(pid) if pid else "Parcel")
    pi = res.get("parcel_info") or {}
    if not pi.get("owner") and cand.get("owner"):
        pi["owner"] = cand["owner"]
    res["parcel_info"] = pi
    if cache_key:
        asyncio.create_task(save_cached_result(cache_key, res))
    return res


def _keep_lead(res: dict, f: LeadFilters) -> bool:
    v = res.get("verdict")
    if v == "ERROR":
        return False
    if f.exclude_kills and v != "PURSUE":
        return False
    if f.road_access and (res.get("parcel_info") or {}).get("road_distance_m") is None:
        return False
    return True


@router.post("/search")
async def search(f: LeadFilters, authorization: Optional[str] = Header(None)):
    denied = await _require_auth(authorization)
    if denied is not None:
        return denied

    # Resolve one or more search centers.
    centers = []
    if f.location:
        g = await _geocode(f.location)
        if g:
            centers.append(g)
    for c in (f.counties or [])[:5]:
        g = await _geocode(str(c) + " County, FL")
        if g:
            centers.append(g)
    if not centers:
        return {"leads": [], "error": "Pick a county (or type an area) to search."}

    # Pull + filter candidates from each center's bbox.
    seen_ids = set()
    cands = []
    for (clat, clng) in centers:
        for c in await _fetch_bbox_parcels(clat, clng):
            pid = c.get("parcel_id")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            if _match(c, f):
                cands.append(c)
    cands.sort(key=lambda c: (c.get("just_value") or 1e12))   # cheaper land first

    if not cands:
        return {"leads": [], "screened": 0, "candidates": 0}

    # Screen with a hard wall-clock deadline so we always return.
    leads = []
    budget = {"n": _MAX_SCREEN}
    t0 = time.monotonic()
    for i in range(0, len(cands), _LEAD_CONCURRENCY):
        chunk = cands[i:i + _LEAD_CONCURRENCY]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[_screen_candidate(c, budget) for c in chunk]),
                timeout=max(3.0, _DEADLINE_S - (time.monotonic() - t0)),
            )
        except asyncio.TimeoutError:
            break
        for res in results:
            if res and _keep_lead(res, f):
                leads.append(res)
        if len(leads) >= _TARGET_LEADS or budget["n"] <= 0 or (time.monotonic() - t0) > _DEADLINE_S:
            break

    leads.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return {"leads": leads[:_TARGET_LEADS], "screened": _MAX_SCREEN - budget["n"], "candidates": len(cands)}
