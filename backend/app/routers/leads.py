"""Land Leads — area-based vacant-land prospecting.

The statewide cadastral only answers spatial (bbox) queries quickly, so we work
from a LOCATION: the user gives a place + filters, we geocode it once (free,
Nominatim), pull every parcel in a small bounding box around it (fast spatial
query), keep the vacant ones matching the filters, and screen those by their
centroid — never geocoding per parcel, so zero paid credits are spent. Each
screen is cached per parcel_id (repeat searches are instant), screening is
bounded + concurrency-capped, and only PURSUE parcels are returned, in the exact
shape of a batch result (the frontend reuses the results table + Deal Review).
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

_VACANT_CODES = {0, 9, 10, 40, 70}     # DOR use codes = vacant/undeveloped land
_BBOX_HALF = 0.02                       # ±0.02deg -> ~4.4km box; small enough that the
                                        # spatial query stays fast even in denser areas
_MAX_CANDIDATES = 70
_MAX_SCREEN = 22                        # hard cap on fresh screens per search
_TARGET_LEADS = 25
_SCREEN_TIMEOUT = 55.0
_BIZ_MARKERS = ("LLC", "L.L.C", "INC", "CORP", "LTD", "TRUST", "PROPERTIES",
                "HOLDINGS", "INVESTMENT", "CAPITAL", "GROUP", "ENTERPRISE",
                "COMPANY", "PARTNERS", "REALTY", "HOMES", "DEVELOPMENT",
                "VENTURES", "ASSOCIATES", "FUND", "BANK", "LP")
_POI_QUERIES = {
    "publix":   '["shop"="supermarket"]["name"~"Publix",i]',
    "walmart":  '["name"~"Walmart|Wal-Mart",i]',
    "grocery":  '["shop"~"supermarket|grocery"]',
    "hospital": '["amenity"="hospital"]',
    "school":   '["amenity"="school"]',
    "highway":  '["highway"~"motorway|trunk"]',
}
_POI_RADIUS_KM = 5.0


class LeadFilters(BaseModel):
    location: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    acres_min: Optional[float] = None
    acres_max: Optional[float] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    owner_type: Optional[str] = None       # 'llc' | 'individual' | 'any'
    near_poi: Optional[str] = None


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


def _is_business(owner: Optional[str]) -> bool:
    o = (owner or "").upper()
    return any(m in o for m in _BIZ_MARKERS)


def _ring_center(ring):
    """Centroid (lat, lng) of an esri ring returned as [lng, lat] pairs."""
    if not ring:
        return None
    sx = sy = 0.0
    n = 0
    for pt in ring:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            sx += pt[0]; sy += pt[1]; n += 1
    return (sy / n, sx / n) if n else None


async def _geocode_location(q: str):
    """Free place lookup (Nominatim) -> (lat, lng). One call per search."""
    if not q:
        return None
    query = q if ("fl" in q.lower() or "florida" in q.lower()) else (q + ", Florida, USA")
    try:
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": "ParcelIQ-Leads/1.0"}) as client:
            r = await client.get("https://nominatim.openstreetmap.org/search",
                                  params={"q": query, "format": "json", "limit": "1", "countrycodes": "us"})
            arr = r.json()
        if arr:
            return float(arr[0]["lat"]), float(arr[0]["lon"])
    except Exception as ex:
        print(f"[Leads] geocode failed for {q!r}: {ex}")
    return None


@router.get("/counties")
async def counties():
    return {"counties": sorted(_CO_NO_TO_COUNTY.values()), "pois": list(_POI_QUERIES.keys())}


async def _fetch_bbox_parcels(lat: float, lng: float) -> list:
    w, s, e, n = lng - _BBOX_HALF, lat - _BBOX_HALF, lng + _BBOX_HALF, lat + _BBOX_HALF
    params = {
        "where": "1=1",
        "geometry": f"{w},{s},{e},{n}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PARCEL_ID,OWN_NAME,PHY_ADDR1,DOR_UC,JV,LND_SQFOOT",
        "returnGeometry": "true",
        "resultRecordCount": "500",
        "f": "json",
    }
    async with httpx.AsyncClient(timeout=22.0) as client:
        r = await client.get(_CAD_URL, params=params)
        r.raise_for_status()
        data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
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
            "acreage": round(sq / 43560, 4) if sq else None,
            "just_value": a.get("JV"),
        })
    return out


def _apply_filters(cands: list, f: LeadFilters) -> list:
    out = []
    ot = (f.owner_type or "any").lower()
    for c in cands:
        ac = c.get("acreage"); jv = c.get("just_value")
        if f.acres_min is not None and (ac is None or ac < f.acres_min):
            continue
        if f.acres_max is not None and (ac is None or ac > f.acres_max):
            continue
        if f.price_min is not None and (jv is None or jv < f.price_min):
            continue
        if f.price_max is not None and (jv is None or jv > f.price_max):
            continue
        if jv is not None and jv <= 100:      # skip nominal-value junk parcels
            continue
        biz = _is_business(c.get("owner"))
        if ot == "llc" and not biz:
            continue
        if ot == "individual" and biz:
            continue
        out.append(c)
    # cheaper land first — usually the better prospect
    out.sort(key=lambda c: (c.get("just_value") or 1e12))
    return out[:_MAX_CANDIDATES]


async def _filter_near_poi(cands: list, poi_key: str, lat: float, lng: float) -> list:
    frag = _POI_QUERIES.get((poi_key or "").lower())
    if not frag or not cands:
        return cands
    s, w, nn, e = lat - _BBOX_HALF - 0.02, lng - _BBOX_HALF - 0.02, lat + _BBOX_HALF + 0.02, lng + _BBOX_HALF + 0.02
    q = f'[out:json][timeout:20];(node{frag}({s},{w},{nn},{e});way{frag}({s},{w},{nn},{e}););out center 300;'
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.post("https://overpass-api.de/api/interpreter", data={"data": q})
            r.raise_for_status()
            elems = r.json().get("elements", [])
    except Exception as ex:
        print(f"[Leads] POI query failed ({ex}) — skipping POI filter")
        return cands
    pts = [(el.get("lat") or (el.get("center") or {}).get("lat"),
            el.get("lon") or (el.get("center") or {}).get("lon")) for el in elems]
    pts = [(a, b) for a, b in pts if a is not None and b is not None]
    if not pts:
        return []
    return [c for c in cands if any(_haversine_km(c["lat"], c["lng"], p[0], p[1]) <= _POI_RADIUS_KM for p in pts)]


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
    lat, lng = f.lat, f.lng
    if lat is None or lng is None:
        geo = await _geocode_location(f.location or "")
        if not geo:
            return {"leads": [], "error": "Couldn't find that location. Try a city or town, e.g. 'Ocala, FL'."}
        lat, lng = geo
    try:
        cands = await _fetch_bbox_parcels(lat, lng)
    except Exception as ex:
        print(f"[Leads] bbox fetch error: {ex}")
        return {"leads": [], "error": "Could not reach the parcel database. Try again."}
    cands = _apply_filters(cands, f)
    if f.near_poi and cands:
        cands = await _filter_near_poi(cands, f.near_poi, lat, lng)
    if not cands:
        return {"leads": [], "screened": 0, "candidates": 0, "center": {"lat": lat, "lng": lng}}

    leads = []
    budget = {"n": _MAX_SCREEN}
    for i in range(0, len(cands), _MAX_PARCEL_CONCURRENCY):
        chunk = cands[i:i + _MAX_PARCEL_CONCURRENCY]
        results = await asyncio.gather(*[_screen_candidate(c, budget) for c in chunk])
        for res in results:
            if res and res.get("verdict") == "PURSUE":
                leads.append(res)
        if len(leads) >= _TARGET_LEADS or budget["n"] <= 0:
            break

    leads.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return {
        "leads": leads[:_TARGET_LEADS],
        "screened": _MAX_SCREEN - budget["n"],
        "candidates": len(cands),
        "center": {"lat": lat, "lng": lng},
    }
