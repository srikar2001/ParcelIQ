"""Land Leads — vacant-land prospecting.

The statewide cadastral only answers small spatial queries quickly (county-wide
attribute scans time out), so we work from geographic CENTERS: for each selected
county (or a typed area) we geocode a center once (free, cached), pull the
parcels in a small bbox around it (fast spatial query), keep the ones matching
the land-type + owner + acreage/value filters, and screen those by centroid —
never geocoding per parcel, so zero paid credits. Each screen is cached per
parcel_id, screening is bounded by a hard deadline + concurrency cap (so a
search ALWAYS returns), and results come back in batch-result shape (frontend
reuses the results table + Deal Review). Optional POI proximity (near a
supermarket / hospital / school / town) is enriched from free OSM Overpass on
the final small lead set only.
"""
import asyncio
import math
import random
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
_SEM_LEADS = asyncio.Semaphore(8)
_LEAD_CONCURRENCY = 8

_VACANT_CODES = {0, 9, 10, 40, 70}
# FL DOR use-code ranges -> the same broad categories the frontend shows, so the
# land-type filter lets an investor widen past vacant (e.g. include agricultural
# or a residential teardown) when they want to.
_CATEGORY_CODES = {
    "Vacant Land": {0, 9, 10, 40, 70},
    "Agricultural": set(range(50, 70)),
    "Residential": set(range(1, 9)),
    "Commercial": set(range(11, 40)),
    "Industrial": set(range(41, 50)),
}
_BBOX_HALF = 0.03                # ±0.03deg ≈ 6.5km box per center — stays fast
_MAX_SCREEN = 24                 # hard cap on fresh screens per search
_TARGET_LEADS = 30
_SCREEN_TIMEOUT = 20.0           # drop a parcel that's slow to screen
_DEADLINE_S = 34.0               # overall screening budget — always return by here
_FETCH_TIMEOUT = 12.0            # per sample-bbox fetch (slow ones are dropped)
_BIZ_MARKERS = ("LLC", "L.L.C", "INC", "CORP", "LTD", "TRUST", "PROPERTIES",
                "HOLDINGS", "INVESTMENT", "CAPITAL", "GROUP", "ENTERPRISE",
                "COMPANY", "PARTNERS", "REALTY", "HOMES", "DEVELOPMENT",
                "VENTURES", "ASSOCIATES", "FUND", "BANK", " LP")

# Free OSM Overpass selectors for the store/hospital/school "near" filter.
# (Town proximity uses the bundled list below — always available, no network.)
_POI_SEL = {
    "supermarket": ('["shop"~"supermarket|department_store|wholesale"]', "Supermarket"),
    "hospital":    ('["amenity"~"hospital|clinic"]', "Hospital"),
    "school":      ('["amenity"~"school|college|university"]', "School"),
    "town":        ('["place"~"town|city"]', "Town"),
}
_OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
# Bundled FL cities/towns (name, lat, lng) so "near a town/city" ALWAYS works
# without depending on a flaky Overpass call — a good spread incl. rural seats.
_FL_PLACES = [
    ("Jacksonville", 30.33, -81.66), ("Miami", 25.76, -80.19), ("Tampa", 27.95, -82.46),
    ("Orlando", 28.54, -81.38), ("Tallahassee", 30.44, -84.28), ("Gainesville", 29.65, -82.32),
    ("Ocala", 29.19, -82.13), ("St. Petersburg", 27.77, -82.64), ("Fort Lauderdale", 26.12, -80.14),
    ("Pensacola", 30.42, -87.22), ("Naples", 26.14, -81.79), ("Fort Myers", 26.64, -81.87),
    ("Sarasota", 27.34, -82.53), ("Lakeland", 28.04, -81.95), ("Daytona Beach", 29.21, -81.02),
    ("Melbourne", 28.08, -80.61), ("Port St. Lucie", 27.27, -80.35), ("West Palm Beach", 26.71, -80.05),
    ("Kissimmee", 28.29, -81.41), ("Cape Coral", 26.56, -81.95), ("Palm Bay", 28.03, -80.59),
    ("Clearwater", 27.97, -82.80), ("Panama City", 30.16, -85.66), ("Key West", 24.56, -81.78),
    ("Lake City", 30.19, -82.64), ("Chiefland", 29.48, -82.86), ("Bronson", 29.45, -82.64),
    ("Perry", 30.12, -83.58), ("Live Oak", 30.29, -82.98), ("Palatka", 29.65, -81.64),
    ("Crystal River", 28.90, -82.59), ("Brooksville", 28.56, -82.39), ("Cross City", 29.63, -83.13),
    ("Trenton", 29.61, -82.82), ("Williston", 29.39, -82.45), ("Inverness", 28.84, -82.33),
    ("Marianna", 30.77, -85.23), ("DeFuniak Springs", 30.72, -86.11), ("Sebring", 27.50, -81.44),
    ("Arcadia", 27.22, -81.86), ("Okeechobee", 27.24, -80.83), ("Wauchula", 27.55, -81.81),
    ("Bushnell", 28.66, -82.11), ("Starke", 29.94, -82.11), ("Macclenny", 30.28, -82.12),
    ("Bartow", 27.90, -81.84), ("Titusville", 28.61, -80.81), ("Stuart", 27.20, -80.25),
    ("Punta Gorda", 26.93, -82.05), ("Fernandina Beach", 30.67, -81.46), ("Newberry", 29.65, -82.61),
    ("Dade City", 28.36, -82.20), ("Wildwood", 28.87, -82.04), ("Tavares", 28.80, -81.73),
]

_county_center_cache: dict = {}


class LeadFilters(BaseModel):
    counties: Optional[list] = None        # list of FL county names
    location: Optional[str] = None         # optional free-text area override
    land_types: Optional[list] = None      # category names; default Vacant Land
    acres_min: Optional[float] = None
    acres_max: Optional[float] = None
    value_min: Optional[float] = None
    value_max: Optional[float] = None
    owner_type: Optional[str] = None       # 'any' | 'individual' | 'business'
    individual_only: bool = False          # legacy alias for owner_type='individual'
    out_of_state: bool = False
    road_access: bool = False
    exclude_kills: bool = True
    poi_types: Optional[list] = None       # subset of _POI_SEL keys
    poi_radius_mi: Optional[float] = None   # required nearness radius (miles)
    limit: Optional[int] = None             # how many leads to generate (1..50)


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


def _codes_for(land_types) -> set:
    if not land_types:
        return set(_VACANT_CODES)
    codes: set = set()
    for lt in land_types:
        codes |= _CATEGORY_CODES.get(lt, set())
    return codes or set(_VACANT_CODES)


def _ring_center(ring):
    if not ring:
        return None
    sx = sy = 0.0
    n = 0
    for pt in ring:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            sx += pt[0]; sy += pt[1]; n += 1
    return (sy / n, sx / n) if n else None


def _haversine_mi(lat1, lng1, lat2, lng2) -> float:
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


async def _geo_full(q: str):
    """Nominatim lookup -> {'lat','lng','bbox':[s,n,w,e] or None}. Cached."""
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
            r0 = arr[0]
            bb = r0.get("boundingbox")
            out = {
                "lat": float(r0["lat"]), "lng": float(r0["lon"]),
                "bbox": [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])] if (bb and len(bb) == 4) else None,
            }
            _county_center_cache[key] = out
            return out
    except Exception as ex:
        print(f"[Leads] geocode failed for {q!r}: {ex}")
    return None


def _grid_points(gf: dict) -> list:
    """Sample a 3x3 grid of centers across a county's bounding box so we cover
    the RURAL parts (where vacant land is), not just the developed county seat.
    Falls back to the single center when no bbox is available."""
    bb = (gf or {}).get("bbox")
    if not bb:
        return [(gf["lat"], gf["lng"])] if gf else []
    s, n, w, e = bb
    pts = []
    for fy in (0.22, 0.5, 0.78):
        for fx in (0.22, 0.5, 0.78):
            pts.append((s + (n - s) * fy, w + (e - w) * fx))
    return pts


@router.get("/counties")
async def counties():
    return {"counties": sorted(_CO_NO_TO_COUNTY.values())}


@router.get("/land-types")
async def land_types():
    return {"land_types": list(_CATEGORY_CODES.keys()),
            "poi_types": [{"key": k, "label": v[1]} for k, v in _POI_SEL.items()]}


async def _fetch_bbox_parcels(lat: float, lng: float, codes: set) -> list:
    w, s, e, n = lng - _BBOX_HALF, lat - _BBOX_HALF, lng + _BBOX_HALF, lat + _BBOX_HALF
    params = {
        "where": "1=1",
        "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "outSR": "4326", "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PARCEL_ID,OWN_NAME,OWN_STATE,PHY_ADDR1,PHY_CITY,DOR_UC,JV,LND_SQFOOT",
        "returnGeometry": "true", "resultRecordCount": "500", "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
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
        if luc not in codes:
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
            "city": (a.get("PHY_CITY") or "").strip() or None,
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
    owner_type = (f.owner_type or ("individual" if f.individual_only else "any")).lower()
    biz = _is_business(c.get("owner"))
    if owner_type == "individual" and biz:
        return False
    if owner_type == "business" and not biz:
        return False
    if f.out_of_state and (c.get("owner_state") in (None, "FL", "")):
        return False
    return True


def _lead_label(cand: dict, pi: dict) -> str:
    """A human address for the row. Real street address when the parcel has one;
    otherwise a descriptive label (vacant land usually has no situs address) so
    the table never shows a bare folio number."""
    addr = (cand.get("address") or "").strip()
    if addr:
        city = (cand.get("city") or "").strip()
        return f"{addr}, {city}" if city else addr
    ac = pi.get("acreage")
    cty = pi.get("county")
    lead = (f"{ac:g}-acre lot" if ac else "Vacant lot")
    return f"{lead} · {cty} County" if cty else lead


async def _screen_candidate(cand: dict, budget: dict) -> Optional[dict]:
    pid = cand.get("parcel_id")
    cache_key = f"lead:{pid}" if pid else None
    res = None
    if cache_key:
        cached = await get_cached_result(cache_key)
        if cached is not None:
            res = cached
    if res is None:
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
        if cache_key:
            asyncio.create_task(save_cached_result(cache_key, res))
    # Post-process fresh AND cached results the same way, so a label/format
    # change takes effect for already-cached parcels without a re-screen.
    res["_lat"] = cand["lat"]; res["_lng"] = cand["lng"]
    pi = res.get("parcel_info") or {}
    if not pi.get("owner") and cand.get("owner"):
        pi["owner"] = cand["owner"]
    res["parcel_info"] = pi
    res["_folio"] = pid
    res["address"] = _lead_label(cand, pi)
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


async def _overpass_places(op_types: list, bbox: tuple):
    """Query Overpass for store/hospital/school points in a bbox. Best-effort:
    curl-style UA (Overpass 406s the default httpx UA), tight timeout, one
    fallback host. Returns {type: [(lat,lng,name)...]} or None if unavailable."""
    if not op_types:
        return {}
    s, w, n, e = bbox
    body = "".join(
        f"node{_POI_SEL[t][0]}({s},{w},{n},{e});way{_POI_SEL[t][0]}({s},{w},{n},{e});"
        for t in op_types
    )
    ql = f"[out:json][timeout:12];({body});out center 500;"
    data = None
    for url in _OVERPASS_URLS:
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "curl/8.4.0"}) as client:
                r = await client.post(url, data={"data": ql})
                r.raise_for_status()
                data = r.json()
            break
        except Exception as ex:
            print(f"[Leads] Overpass {url} failed: {ex}")
    if not data:
        return None

    def _classify(tags: dict):
        if tags.get("shop") in ("supermarket", "department_store", "wholesale"):
            return "supermarket"
        if tags.get("amenity") in ("hospital", "clinic"):
            return "hospital"
        if tags.get("amenity") in ("school", "college", "university"):
            return "school"
        return None

    out: dict = {t: [] for t in op_types}
    for el in data.get("elements", []):
        tags = el.get("tags", {}) or {}
        kind = _classify(tags)
        if kind not in out:
            continue
        if el.get("type") == "node":
            plat, plng = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            plat, plng = c.get("lat"), c.get("lon")
        if plat is None or plng is None:
            continue
        out[kind].append((plat, plng, (tags.get("name") or _POI_SEL[kind][1])))
    return out


async def _enrich_poi(leads: list, poi_types, radius_mi):
    """Attach nearest supermarket/hospital/school/town distances to each lead and
    keep only leads with at least one requested place within `radius_mi`. Town
    proximity uses the bundled FL list (always works); the rest use Overpass
    best-effort. Returns (leads, ok) — ok=False means an Overpass type was
    requested but unavailable, so we did NOT filter on it (kept all)."""
    types = [t for t in (poi_types or []) if t in _POI_SEL]
    pts = [(l, l.get("_lat"), l.get("_lng")) for l in leads
           if l.get("_lat") is not None and l.get("_lng") is not None]
    if not types or not pts:
        return leads, True, {}
    radius = radius_mi or 10.0

    places: dict = {t: [] for t in types}
    if "town" in types:
        places["town"] = [(la, lo, nm) for nm, la, lo in _FL_PLACES]

    op_types = [t for t in types if t != "town"]
    ok = True
    if op_types:
        lats = [p[1] for p in pts]; lngs = [p[2] for p in pts]
        pad = radius / 69.0 + 0.05
        bbox = (min(lats) - pad, min(lngs) - pad, max(lats) + pad, max(lngs) + pad)
        op = await _overpass_places(op_types, bbox)
        if op is None:
            ok = False           # service down — don't filter these out
        else:
            for t in op_types:
                places[t] = op.get(t) or []

    # Types we can actually evaluate (town always; Overpass types only if ok).
    eval_types = [t for t in types if t == "town" or ok]

    kept = []
    for l, la, lo in pts:
        near = {}
        best = None
        for t in types:
            cand_pts = places.get(t) or []
            if not cand_pts:
                continue
            nd = min(cand_pts, key=lambda p: _haversine_mi(la, lo, p[0], p[1]))
            dist = round(_haversine_mi(la, lo, nd[0], nd[1]), 1)
            near[t] = {"name": nd[2], "label": _POI_SEL[t][1], "dist_mi": dist,
                       "lat": nd[0], "lng": nd[1]}
            if t in eval_types:
                best = dist if best is None else min(best, dist)
        if near:
            l.setdefault("parcel_info", {})["poi"] = near
        # Keep if within radius of an evaluable type, OR if we couldn't evaluate
        # any type (all requested were down) — never silently drop everything.
        if (best is not None and best <= radius) or not eval_types:
            kept.append(l)

    # POI points to draw on the map (deduped by rounded coord, capped per type).
    poi_points: dict = {}
    for t in types:
        seen = set(); out = []
        for la, lo, nm in (places.get(t) or []):
            key = (round(la, 4), round(lo, 4))
            if key in seen:
                continue
            seen.add(key)
            out.append({"lat": la, "lng": lo, "name": nm, "label": _POI_SEL[t][1]})
            if len(out) >= 80:
                break
        if out:
            poi_points[t] = out
    return kept, ok, poi_points


@router.post("/search")
async def search(f: LeadFilters, authorization: Optional[str] = Header(None)):
    denied = await _require_auth(authorization)
    if denied is not None:
        return denied

    codes = _codes_for(f.land_types)

    # Resolve sample points — a grid across each county (so we hit the rural
    # areas where vacant land actually is), plus any free-text area.
    points = []
    if f.location:
        gf = await _geo_full(f.location)
        if gf:
            points.append((gf["lat"], gf["lng"]))
    for c in (f.counties or [])[:4]:
        gf = await _geo_full(str(c) + " County, FL")
        if gf:
            points.extend(_grid_points(gf))
    if not points:
        return {"leads": [], "error": "Pick a county (or type an area) to search."}
    random.shuffle(points)
    points = points[:8]     # cap total bbox fetches per search

    # Fetch every sample bbox concurrently, then dedupe + filter to candidates.
    fetched = await asyncio.gather(*[_fetch_bbox_parcels(p[0], p[1], codes) for p in points])
    seen_ids = set()
    cands = []
    for parcels in fetched:
        for c in parcels:
            pid = c.get("parcel_id")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            if _match(c, f):
                cands.append(c)
    cands.sort(key=lambda c: (c.get("just_value") or 1e12))   # cheaper land first

    if not cands:
        return {"leads": [], "screened": 0, "candidates": 0}

    # How many leads to generate (user-chosen; deadline still bounds the work).
    target = max(1, min(int(f.limit or _TARGET_LEADS), 50))
    max_screen = min(max(target + 8, _MAX_SCREEN), 60)

    # Screen with a hard wall-clock deadline so we always return.
    leads = []
    budget = {"n": max_screen}
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
        if len(leads) >= target or budget["n"] <= 0 or (time.monotonic() - t0) > _DEADLINE_S:
            break

    # Optional: keep only leads near a chosen public place (+ points to map).
    poi_degraded = False
    poi_points = {}
    if f.poi_types:
        leads, poi_ok, poi_points = await _enrich_poi(leads, f.poi_types, f.poi_radius_mi)
        poi_degraded = not poi_ok

    leads.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return {"leads": leads[:target], "screened": max_screen - budget["n"],
            "candidates": len(cands), "poi_degraded": poi_degraded, "poi_points": poi_points}
