"""Land Leads — turn the whole FL cadastral into a prospecting tool.

The user picks filters (county, acreage, price, owner type, a nearby anchor like
Publix/Walmart/a hospital); we pull matching VACANT-land parcels straight from
the county cadastral (free, instant), then screen the candidates and return only
the PURSUE ones in the exact same shape as a batch result (so the frontend
reuses the results table + Deal Review verbatim).

Optimized on purpose:
  * Coordinates come from the cadastral centroid, so we NEVER geocode — zero
    paid geocoding credits are spent, ever.
  * Every screen is cached per parcel_id, so repeat/overlapping searches are
    instant and the catalog only gets faster with use.
  * Screening is bounded (a hard per-search cap) and runs under the same global
    concurrency semaphore the batch path uses, so it can't overload the DB.
  * It never touches the user's weekly screening limit.
"""
import asyncio
from typing import Optional

import httpx
from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.collectors.parcel_fl import URL as _CAD_URL, _CO_NO_TO_COUNTY
from app.routers.batch import (
    _screen_coordinate, _SEM_PARCELS, _MAX_PARCEL_CONCURRENCY,
    _user_id_from_token, _AuthUnavailable,
)
from app.core.cache import get_cached_result, save_cached_result

router = APIRouter(prefix="/api/leads")

_COUNTY_TO_CO_NO = {v.lower(): k for k, v in _CO_NO_TO_COUNTY.items()}
# DOR use codes that mean vacant/undeveloped land (mirrors explore _lu_category).
_VACANT_CODES = "0,9,10,40,70"
_MAX_CANDIDATES = 120       # how many matching parcels to pull from the cadastral
_MAX_SCREEN = 22            # hard cap on screens per search (bounds latency + DB load)
_TARGET_LEADS = 25
_SCREEN_TIMEOUT = 55.0
# Business-owner name markers — LLC / entity vs an individual.
_BIZ_MARKERS = ("LLC", "L L C", "INC", "CORP", "LTD", " LP", "L P ", "TRUST",
                "PROPERTIES", "HOLDINGS", "INVESTMENT", "CAPITAL", "GROUP",
                "ENTERPRISE", "COMPANY", " CO ", "PARTNERS", "REALTY", "HOMES",
                "DEVELOPMENT", "VENTURES", "ASSOCIATES", "FUND", "BANK")
# Nearby-anchor presets -> Overpass query fragment.
_POI_QUERIES = {
    "publix":   '["shop"="supermarket"]["name"~"Publix",i]',
    "walmart":  '["name"~"Walmart|Wal-Mart",i]',
    "grocery":  '["shop"~"supermarket|grocery"]',
    "hospital": '["amenity"="hospital"]',
    "school":   '["amenity"="school"]',
    "highway":  '["highway"~"motorway|trunk"]',
}
_POI_RADIUS_KM = 5.0        # "nearby" = within this many km of the anchor


class LeadFilters(BaseModel):
    county: Optional[str] = None
    acres_min: Optional[float] = None
    acres_max: Optional[float] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    owner_type: Optional[str] = None       # 'llc' | 'individual' | 'any'
    near_poi: Optional[str] = None         # key of _POI_QUERIES
    offset: Optional[int] = 0              # for "load more"


async def _require_auth(authorization: Optional[str]) -> Optional[JSONResponse]:
    token = (authorization or "").removeprefix("Bearer ").strip()
    try:
        ident = await _user_id_from_token(token)
    except _AuthUnavailable:
        return None
    if not ident:
        return JSONResponse(status_code=401, content={"error": "Sign in to generate leads."})
    return None


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    import math
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, a ** 0.5))


def _build_where(f: LeadFilters) -> str:
    clauses = [f"DOR_UC IN ({_VACANT_CODES})", "JV > 1000"]
    co = _COUNTY_TO_CO_NO.get((f.county or "").strip().lower()) if f.county else None
    if co:
        clauses.append(f"CO_NO={co}")
    if f.acres_min:
        clauses.append(f"LND_SQFOOT >= {int(f.acres_min * 43560)}")
    if f.acres_max:
        clauses.append(f"LND_SQFOOT <= {int(f.acres_max * 43560)}")
    if f.price_min:
        clauses.append(f"JV >= {int(f.price_min)}")
    if f.price_max:
        clauses.append(f"JV <= {int(f.price_max)}")
    ot = (f.owner_type or "any").lower()
    if ot == "llc":
        ors = " OR ".join(f"OWN_NAME LIKE '%{m.strip()}%'" for m in _BIZ_MARKERS if m.strip())
        clauses.append(f"({ors})")
    elif ot == "individual":
        ands = " AND ".join(f"OWN_NAME NOT LIKE '%{m.strip()}%'" for m in ("LLC", "INC", "CORP", "TRUST", "PROPERTIES", "HOLDINGS", "LTD"))
        clauses.append(f"({ands})")
    return " AND ".join(clauses)


def _is_business(owner: Optional[str]) -> bool:
    o = (owner or "").upper()
    return any(m in o for m in _BIZ_MARKERS)


@router.get("/counties")
async def counties():
    """FL county list for the filter dropdown."""
    return {"counties": sorted(_CO_NO_TO_COUNTY.values()), "pois": list(_POI_QUERIES.keys())}


async def _fetch_candidates(f: LeadFilters) -> list:
    params = {
        "where": _build_where(f),
        "outFields": "PARCEL_ID,OWN_NAME,PHY_ADDR1,PHY_CITY,DOR_UC,JV,LND_SQFOOT,CO_NO",
        "returnGeometry": "false",
        "returnCentroid": "true",
        "outSR": "4326",
        "orderByFields": "JV ASC",   # cheaper land first — better prospects
        "resultOffset": str(max(0, f.offset or 0)),
        "resultRecordCount": str(_MAX_CANDIDATES),
        "f": "json",
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.get(_CAD_URL, params=params)
        r.raise_for_status()
        data = r.json()
    out = []
    for feat in data.get("features", []):
        a = feat.get("attributes", {})
        c = feat.get("centroid") or {}
        if c.get("y") is None or c.get("x") is None:
            continue
        sq = a.get("LND_SQFOOT")
        out.append({
            "parcel_id": a.get("PARCEL_ID"),
            "lat": c["y"], "lng": c["x"],
            "address": (a.get("PHY_ADDR1") or "").strip() or None,
            "city": (a.get("PHY_CITY") or "").strip() or None,
            "owner": (a.get("OWN_NAME") or "").strip() or None,
            "acreage": round(sq / 43560, 3) if sq else None,
            "just_value": a.get("JV"),
        })
    return out


async def _filter_near_poi(cands: list, poi_key: str) -> list:
    """Keep only candidates within _POI_RADIUS_KM of the requested anchor.
    One Overpass query for the candidates' bounding box (not per-parcel)."""
    frag = _POI_QUERIES.get((poi_key or "").lower())
    if not frag or not cands:
        return cands
    lats = [c["lat"] for c in cands]; lngs = [c["lng"] for c in cands]
    pad = 0.06
    s, n = min(lats) - pad, max(lats) + pad
    w, e = min(lngs) - pad, max(lngs) + pad
    q = f'[out:json][timeout:20];(node{frag}({s},{w},{n},{e});way{frag}({s},{w},{n},{e}););out center 400;'
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.post("https://overpass-api.de/api/interpreter", data={"data": q})
            r.raise_for_status()
            elems = r.json().get("elements", [])
    except Exception as ex:
        print(f"[Leads] POI query failed ({ex}) — skipping POI filter")
        return cands
    pts = []
    for el in elems:
        plat = el.get("lat") or (el.get("center") or {}).get("lat")
        plng = el.get("lon") or (el.get("center") or {}).get("lon")
        if plat is not None and plng is not None:
            pts.append((plat, plng))
    if not pts:
        return []
    kept = []
    for c in cands:
        if any(_haversine_km(c["lat"], c["lng"], p[0], p[1]) <= _POI_RADIUS_KM for p in pts):
            kept.append(c)
    return kept


async def _screen_candidate(cand: dict, new_screen_budget: dict) -> Optional[dict]:
    """Return the parcel's screen result — cached if we've seen it, otherwise a
    fresh centroid screen (counted against the per-search screen budget)."""
    pid = cand.get("parcel_id")
    cache_key = f"lead:{pid}" if pid else None
    if cache_key:
        cached = await get_cached_result(cache_key)
        if cached is not None:
            return cached
    # Only spend a real screen if we still have budget (bounds latency + load).
    if new_screen_budget["n"] <= 0:
        return None
    new_screen_budget["n"] -= 1
    async with _SEM_PARCELS:
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


@router.post("/search")
async def search(f: LeadFilters, authorization: Optional[str] = Header(None)):
    denied = await _require_auth(authorization)
    if denied is not None:
        return denied
    try:
        cands = await _fetch_candidates(f)
    except Exception as ex:
        print(f"[Leads] cadastral fetch error: {ex}")
        return {"leads": [], "error": "Could not reach the parcel database. Try again."}
    if not cands:
        return {"leads": [], "screened": 0, "candidates": 0}
    if f.near_poi:
        cands = await _filter_near_poi(cands, f.near_poi)

    leads = []
    budget = {"n": _MAX_SCREEN}
    # Process in concurrency-sized chunks so we can stop as soon as we have
    # enough PURSUE leads (or run out of screen budget).
    for i in range(0, len(cands), _MAX_PARCEL_CONCURRENCY):
        chunk = cands[i:i + _MAX_PARCEL_CONCURRENCY]
        results = await asyncio.gather(*[_screen_candidate(c, budget) for c in chunk])
        for res in results:
            if res and res.get("verdict") == "PURSUE":
                leads.append(res)
        if len(leads) >= _TARGET_LEADS or budget["n"] <= 0:
            break

    leads.sort(key=lambda x: x.get("score") or 0, reverse=True)
    leads = leads[:_TARGET_LEADS]
    return {"leads": leads, "screened": _MAX_SCREEN - budget["n"], "candidates": len(cands)}
