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
import json
import math
import os
import random
import re
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Bundled, simplified real FL county boundaries (Census TIGER, RDP-simplified) so
# the Land Leads map draws true county borders — not bbox rectangles — for every
# county instantly, with zero live geocoding / rate limits.
_FL_COUNTY_POLYS: dict = {}
try:
    _cp = os.path.join(os.path.dirname(__file__), "..", "..", "data", "fl_counties.json")
    with open(_cp, "r") as _f:
        _FL_COUNTY_POLYS = json.load(_f)
except Exception as _e:  # never let a missing file break the leads API
    print(f"[Leads] county polygons unavailable: {_e}")

from app.collectors.parcel_fl import URL as _CAD_URL, _CO_NO_TO_COUNTY
from app.collectors.flood import get_flood_zone
from app.routers.batch import _screen_coordinate, _user_id_from_token, _AuthUnavailable
from app.core.cache import get_cached_result, save_cached_result

router = APIRouter(prefix="/api/leads")

# Bounded concurrency for lead screening (a search only ever screens _MAX_SCREEN
# parcels, so a modest bump over the batch path is safe for the DB).
_SEM_LEADS = asyncio.Semaphore(18)
_LEAD_CONCURRENCY = 18

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
_MAX_SCREEN = 40                 # hard cap on fresh screens per search (was 24 —
                                 # too low to fill a 50-lead request in leaner counties)
_FLOOD_PRECHECK = 80             # candidates to cheaply flood-check before screening
_TARGET_LEADS = 30
_SCREEN_TIMEOUT = 20.0           # drop a parcel that's slow to screen
_DEADLINE_S = 48.0               # overall screening budget — always return by here
                                 # (frontend aborts at 90s; loop still stops early
                                 # once the requested lead count is reached)
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


# FL DOR PHY_ADDR1 is full of placeholders for vacant land ("UNASSIGNED
# LOCATION", "NO SITUS", "0 NW ...") — none are real, deliverable addresses.
_ADDR_JUNK = ("UNASSIGNED", "NO SITUS", "NOSITUS", "NOT ASSIGNED", "UNKNOWN",
              "NO NAME", "NONE", "N/A", "TBD", "NO ADDRESS", "NO STREET",
              "VACANT", "MULTIPLE")


def _clean_situs(addr: Optional[str]) -> Optional[str]:
    """Real deliverable street address, or None for the DOR placeholder junk that
    plagues vacant-land situs fields — so the row shows a clean
    '<acres>-acre lot · <County>' label instead of 'UNASSIGNED LOCATION RE'."""
    if not addr:
        return None
    a = " ".join(str(addr).split())
    up = a.upper()
    if any(j in up for j in _ADDR_JUNK):
        return None
    m = re.match(r"^(\d+)\b", a)
    if not m or int(m.group(1)) == 0:      # a real situs needs a house number > 0
        return None
    return a


_CITY_JUNK = {"UNINCORPORATED COUNTY", "UNINCORPORATED", "UNINCORP",
              "UNINCORPORATED AREA", "NONE", "N/A", "NULL"}


def _clean_city(city) -> Optional[str]:
    """Drop non-city PHY_CITY values (Miami-Dade stores 'UNINCORPORATED COUNTY')
    so a row reads '17900 SW 174 ST' instead of '..., Unincorporated County'."""
    c = (city or "").strip()
    return None if (not c or c.upper() in _CITY_JUNK) else c


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
    dy, dx = (n - s), (e - w)
    pts = []
    # 4x4 = 16 cells across the county (was 3x3) so we cover far MORE of the county
    # — vast searches, not a few windows. Jitter each cell ±5% of the county span
    # so two searches explore DIFFERENT areas (variety) instead of the same points.
    grid = (0.12, 0.31, 0.5, 0.69, 0.88)   # 5x5 = 25 cells — even broader coverage
    # Small ±4% jitter: enough that the exact windows move run-to-run (variety),
    # but not so much that a cell over the productive interior drifts out into
    # the water/Everglades (which made one run hit the coast → 0, the next hit
    # the dry SW → 46). Run-to-run VARIETY comes mainly from the dry-candidate
    # shuffle at screen time; coverage stays consistent here.
    for fy in grid:
        for fx in grid:
            jy = (random.random() - 0.5) * dy * 0.04
            jx = (random.random() - 0.5) * dx * 0.04
            pts.append((s + dy * fy + jy, w + dx * fx + jx))
    random.shuffle(pts)
    return pts


@router.get("/counties")
async def counties():
    return {"counties": sorted(_CO_NO_TO_COUNTY.values())}


def _poly_bbox_center(poly: dict):
    """[s,n,w,e] bbox + centroid-ish center from a bundled county polygon
    ({'t','c'} where c is [lng,lat] rings)."""
    rings = poly["c"] if poly["t"] == "Polygon" else [r for mp in poly["c"] for r in mp]
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    if not xs:
        return None, None, None
    w, e, s, n = min(xs), max(xs), min(ys), max(ys)
    return [s, n, w, e], (s + n) / 2, (w + e) / 2


@router.get("/county-geo")
async def county_geo(names: str = ""):
    """Real county border polygon + bbox for each selected county, served from
    the bundled boundaries so the Land Leads map draws true borders for ALL
    selected counties instantly (even "select all"). Falls back to the cached
    geocoder only for a typed area that isn't one of the 67 counties."""
    out, seen = [], set()
    for nm in [n.strip() for n in names.split(",") if n.strip()][:70]:
        k = nm.lower()
        if k in seen:
            continue
        seen.add(k)
        poly = _FL_COUNTY_POLYS.get(nm)
        if poly:
            bbox, clat, clng = _poly_bbox_center(poly)
            out.append({"name": nm, "lat": clat, "lng": clng, "bbox": bbox,
                        "polygon": {"t": poly["t"], "c": poly["c"]}})
            continue
        gf = await _geo_full(nm + " County, FL")
        if gf:
            out.append({"name": nm, "lat": gf["lat"], "lng": gf["lng"], "bbox": gf.get("bbox")})
    return {"counties": out}


@router.get("/land-types")
async def land_types():
    return {"land_types": list(_CATEGORY_CODES.keys()),
            "poi_types": [{"key": k, "label": v[1]} for k, v in _POI_SEL.items()]}


def _uc_where(codes: set, f: "LeadFilters | None") -> str:
    """Server-side attribute filter so each bbox page returns MATCHING vacant
    parcels instead of mostly houses (DOR_UC is a string field, and different
    counties zero-pad differently, so match both '9' and '09' forms). Acreage /
    value push down as numeric ranges on the Double fields."""
    vals = set()
    for c in sorted(codes):
        vals.add(f"'{c}'")
        vals.add(f"'{c:02d}'")
        vals.add(f"'{c:03d}'")   # DOR_UC is stored 3-char zero-padded ('009','040','070')
    where = "DOR_UC IN (" + ",".join(sorted(vals)) + ")"
    if f is not None:
        if f.acres_min:
            where += f" AND LND_SQFOOT >= {int(f.acres_min * 43560)}"
        if f.acres_max:
            where += f" AND LND_SQFOOT <= {int(f.acres_max * 43560)}"
        if f.value_min:
            where += f" AND JV >= {int(f.value_min)}"
        if f.value_max:
            where += f" AND JV <= {int(f.value_max)}"
    return where


async def _fetch_bbox_parcels(lat: float, lng: float, codes: set,
                              f: "LeadFilters | None" = None, use_filter: bool = True) -> list:
    w, s, e, n = lng - _BBOX_HALF, lat - _BBOX_HALF, lng + _BBOX_HALF, lat + _BBOX_HALF
    where = _uc_where(codes, f) if use_filter else "1=1"
    params = {
        "where": where,
        "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "outSR": "4326", "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PARCEL_ID,CO_NO,OWN_NAME,OWN_STATE,PHY_ADDR1,PHY_CITY,DOR_UC,JV,LND_SQFOOT",
        # 2000-row pages so a bbox returns the real matching set, not the first
        # 500 records (which under the old unfiltered scan were almost all houses).
        "returnGeometry": "true", "resultRecordCount": "2000", "f": "json",
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
    feats = data.get("features", [])
    out = []
    for feat in feats:
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
        # Keep the parcel's actual boundary — this is the authoritative outline
        # for the lot we picked, so the Deal Review draws the RIGHT parcel
        # instead of whatever the coordinate re-screen resolves. Nested
        # [[[lat,lng],...]] (list of rings) matches the batch collector's format
        # that the frontend's extractPolygonCoords() expects (bbox is outSR=4326,
        # so rings are already lng/lat degrees — just swap to lat/lng).
        geom = [[[pt[1], pt[0]] for pt in ring
                 if isinstance(pt, (list, tuple)) and len(pt) >= 2]
                for ring in rings] if rings else []
        sq = a.get("LND_SQFOOT")
        out.append({
            "parcel_id": a.get("PARCEL_ID"),
            "co_no": a.get("CO_NO"),
            "lat": center[0], "lng": center[1],
            "geometry": geom,
            "address": _clean_situs(a.get("PHY_ADDR1")),
            "city": _clean_city(a.get("PHY_CITY")),
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
    # Quality floor: when the user hasn't asked for tiny lots, drop slivers /
    # retention ponds / road remnants (< ~0.08 acre) — never real land deals.
    if f.acres_min is None and ac is not None and ac < 0.08:
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


async def _reverse_geocode(lat: float, lng: float) -> Optional[str]:
    """A real street reference for a lot with NO situs address, via Photon (free,
    OSM-based) — cached. Street-level only (no house number) so we never imply a
    specific building on a vacant lot: 'SW 313th St, Homestead'."""
    key = f"revgeo:{round(lat, 5)},{round(lng, 5)}"
    try:
        cached = await get_cached_result(key)
    except Exception:
        cached = None
    if cached:                       # non-empty hit only — never cache a failure,
        return cached                # so a transient miss doesn't blank forever
    addr = None
    try:
        # A real User-Agent — the default httpx UA gets blocked server-side (same
        # trap as Overpass), which is why reverse-geo silently failed from Railway.
        async with httpx.AsyncClient(timeout=6.0,
                                     headers={"User-Agent": "ParcelIQ/1.0 (+leads)"}) as client:
            r = await client.get("https://photon.komoot.io/reverse",
                                  params={"lat": lat, "lon": lng, "lang": "en"})
            props = ((r.json().get("features") or [{}])[0]).get("properties") or {}
        st = props.get("street") or props.get("name") or props.get("district")
        city = (props.get("city") or props.get("town") or props.get("village")
                or props.get("locality") or props.get("county"))
        parts = [p for p in (st, city) if p]
        addr = ", ".join(parts) if parts else None
    except Exception:
        addr = None
    if addr:
        try:
            asyncio.create_task(save_cached_result(key, addr))
        except Exception:
            pass
    return addr


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
    # Screening re-derives parcel_info from the COORDINATE, which can resolve a
    # neighboring parcel (wrong address/owner/acreage/outline). The cadastral
    # record we actually selected is authoritative — overlay it so the row AND
    # the Deal Review show the exact same, correct lot.
    pi["parcel_id"] = pid or pi.get("parcel_id")
    if cand.get("owner"):
        pi["owner"] = cand["owner"]
    if cand.get("acreage") is not None:
        pi["acreage"] = cand["acreage"]
    if cand.get("just_value") is not None:
        pi["just_value"] = cand["just_value"]
        pi["assessed_value"] = pi.get("assessed_value") or cand["just_value"]
    if cand.get("geometry"):
        pi["geometry"] = cand["geometry"]
    if cand.get("address"):
        full = cand["address"] + (", " + cand["city"] if cand.get("city") else "")
        pi["county_address_on_file"] = full
    else:
        # No real situs on the selected parcel → don't let screening's own
        # placeholder ("NO SITUS, OCALA") leak into the Deal Review either.
        pi["county_address_on_file"] = None
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


def _cand_stub(cand: dict) -> dict:
    """A matching parcel we didn't get to fully screen — shaped as a minimal lead
    so the user still sees the real lot (verdict/score resolve when they open it)."""
    county = None
    try:
        if cand.get("co_no") is not None:
            county = _CO_NO_TO_COUNTY.get(int(cand["co_no"]))
    except (TypeError, ValueError):
        pass
    pi = {
        "county": county, "parcel_id": cand.get("parcel_id"),
        "acreage": cand.get("acreage"), "owner": cand.get("owner"),
        "just_value": cand.get("just_value"), "geometry": cand.get("geometry"),
    }
    res = {"verdict": None, "score": None, "flags": [], "positives": [], "sources": [],
           "parcel_info": pi, "_lat": cand["lat"], "_lng": cand["lng"], "_folio": cand.get("parcel_id")}
    res["address"] = _lead_label(cand, pi)
    return res


def _log_search(f: LeadFilters, candidates: int, screened: int, leads: list, degraded) -> None:
    """One line per search so a 0-result is a diagnosable event, not a mystery."""
    pursue = sum(1 for l in leads if (l.get("verdict") == "PURSUE"))
    print(f"[Leads] counties={f.counties} types={f.land_types} "
          f"exclude_kills={f.exclude_kills} candidates={candidates} screened={screened} "
          f"pursue={pursue} kept={len(leads)} degraded={degraded}")


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

    # POI points to draw on the map — only those actually NEAR a lead (so the
    # bundled statewide town list doesn't scatter pins across all of Florida).
    lead_ll = [(la, lo) for _, la, lo in pts]
    near_cap = radius * 1.25
    poi_points: dict = {}
    for t in types:
        seen = set(); out = []
        for la, lo, nm in (places.get(t) or []):
            if lead_ll and min(_haversine_mi(la, lo, pla, plo) for pla, plo in lead_ll) > near_cap:
                continue
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
    # Sample a grid across each selected county. Prefer the bundled REAL county
    # extent (accurate, instant, no flaky geocode) and fall back to Nominatim
    # only for a county we somehow don't have bundled.
    sel_counties = (f.counties or [])[:8]
    for c in sel_counties:
        poly = _FL_COUNTY_POLYS.get(str(c))
        if poly:
            bbox, clat, clng = _poly_bbox_center(poly)
            points.extend(_grid_points({"lat": clat, "lng": clng, "bbox": bbox}))
        else:
            gf = await _geo_full(str(c) + " County, FL")
            if gf:
                points.extend(_grid_points(gf))
    if not points:
        return {"leads": [], "error": "Pick a county (or type an area) to search."}
    random.shuffle(points)
    # Cap total bbox fetches for a bounded runtime. Wider than before (a single
    # county now uses up to ~16 windows, not ~8) so the search is VAST — it covers
    # much more of the county, and the shuffle above means a different subset each
    # run → two people searching the same county get different lots.
    points = points[:min(30, 20 + 5 * len(sel_counties))]

    # Fetch every sample bbox concurrently with the tight server-side filter,
    # then dedupe + filter to candidates.
    async def _gather(use_filter: bool):
        got = await asyncio.gather(*[_fetch_bbox_parcels(p[0], p[1], codes, f, use_filter) for p in points])
        ids, cs = set(), []
        for parcels in got:
            for c in parcels:
                pid = c.get("parcel_id")
                if pid in ids:
                    continue
                ids.add(pid)
                if _match(c, f):
                    cs.append(c)
        return cs

    cands = await _gather(True)
    # Safety net: if the server-side attribute filter matched nothing across the
    # whole search (e.g. a county whose DOR_UC codes are zero-padded differently),
    # fall back once to the unfiltered scan + client-side filtering. Guarantees a
    # search never silently returns empty because of a where-clause mismatch.
    if not cands:
        cands = await _gather(False)
    # Rank the parcels we'll screen. For pure vacant-land searches, screen the most
    # buildable-looking first (a real situs address + healthy $/acre correlate with
    # dry, road-accessible land; swamp/wetland is near-worthless + unaddressed). But
    # that heuristic is HOSTILE to agricultural / large-acreage plays (rural land is
    # unaddressed and low $/acre), so when the search includes those, rank by size —
    # biggest & cheapest first — so those lots actually get screened instead of
    # sinking below the screen budget and returning nothing.
    lt = set(f.land_types or [])
    buildable_first = (not lt) or lt == {"Vacant Land"}
    def _cand_rank(c: dict):
        ac = c.get("acreage") or 0.0
        jv = c.get("just_value") or 0.0
        if buildable_first:
            vpa = (jv / ac) if ac > 0 else 0.0
            has_addr = 1 if c.get("address") else 0
            return (-has_addr, -min(vpa, 40000.0), jv)
        return (-ac, jv)   # ag / mixed: biggest lots first, cheapest as tiebreak
    cands.sort(key=_cand_rank)

    if not cands:
        _log_search(f, 0, 0, [], None)
        return {"leads": [], "screened": 0, "candidates": 0, "degraded": None}

    # De-flood pre-filter. The screen budget is small (~24 parcels), so SPEND IT ON
    # DRY LAND. The flood layer is in-house (fast), so cheaply check the top
    # candidates and screen non-flood ones FIRST — otherwise a coastal county
    # (Miami-Dade, the Keys) burns every screen on Flood-AE lots and returns
    # all-KILL. Shuffle within each bucket so two searches surface DIFFERENT lots.
    head = cands[:_FLOOD_PRECHECK]
    try:
        fzs = await asyncio.wait_for(
            asyncio.gather(*[get_flood_zone(c["lat"], c["lng"]) for c in head],
                           return_exceptions=True),
            timeout=8.0)
    except Exception:
        fzs = []
    if len(fzs) != len(head):
        fzs = [None] * len(head)
    dry, wet = [], []
    for c, fz in zip(head, fzs):
        (wet if (isinstance(fz, dict) and fz.get("sfha") is True) else dry).append(c)
    random.shuffle(dry)
    random.shuffle(wet)
    cands = dry + wet + cands[_FLOOD_PRECHECK:]

    # How many leads to generate (user-chosen; deadline still bounds the work).
    target = max(1, min(int(f.limit or _TARGET_LEADS), 50))
    max_screen = min(max(target + 8, _MAX_SCREEN), 60)

    # Screen with a hard wall-clock deadline so we always return.
    leads = []
    screened_all = []
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
            if not res:
                continue
            screened_all.append(res)
            if _keep_lead(res, f):
                leads.append(res)
        if len(leads) >= target or budget["n"] <= 0 or (time.monotonic() - t0) > _DEADLINE_S:
            break

    # NEVER a dead end. If the PURSUE-only filter left nothing (e.g. a wetland-heavy
    # county where everything we checked flags), surface what we DID screen with
    # their real verdicts; if the deadline hit before anything screened, shape the
    # top matching parcels as unscreened leads. Either way the user sees real lots
    # (the frontend shows a "nothing fully cleared" note for the degraded case).
    degraded = None
    if not leads:
        if screened_all:
            screened_all.sort(key=lambda x: x.get("score") or -1, reverse=True)
            leads = screened_all
            degraded = "no_pursue"
        elif cands:
            leads = [_cand_stub(c) for c in cands[:target]]
            degraded = "unscreened"

    # Optional: keep only leads near a chosen public place (+ points to map).
    poi_degraded = False
    poi_points = {}
    if f.poi_types:
        leads, poi_ok, poi_points = await _enrich_poi(leads, f.poi_types, f.poi_radius_mi)
        poi_degraded = not poi_ok

    leads.sort(key=lambda x: x.get("score") or -1, reverse=True)
    leads = leads[:target]

    # Give addressless vacant leads a real street reference (free reverse-geo,
    # cached) so a row reads "SW 313th St, Homestead · Miami-Dade County" instead
    # of just "5-acre lot · Miami-Dade County". Bounded to the returned leads.
    geo_targets = [l for l in leads
                   if l.get("_lat") is not None
                   and isinstance(l.get("address"), str)
                   and ("-acre lot" in l["address"] or l["address"].startswith("Vacant lot"))][:24]
    if geo_targets:
        _rg_sem = asyncio.Semaphore(8)   # throttle so a big batch doesn't get rate-limited

        async def _rg(l):
            async with _rg_sem:
                return await _reverse_geocode(l["_lat"], l["_lng"])
        try:
            geos = await asyncio.wait_for(
                asyncio.gather(*[_rg(l) for l in geo_targets], return_exceptions=True),
                timeout=14.0)
        except Exception:
            geos = []
        for l, g in zip(geo_targets, geos):
            if isinstance(g, str) and g:
                cty = (l.get("parcel_info") or {}).get("county")
                l["address"] = g + (f" · {cty} County" if (cty and cty.lower() not in g.lower()) else "")

    _log_search(f, len(cands), max_screen - budget["n"], leads, degraded)
    return {"leads": leads, "screened": max_screen - budget["n"],
            "candidates": len(cands), "poi_degraded": poi_degraded,
            "poi_points": poi_points, "degraded": degraded}
