"""Map Comps / Parcel Exploration.

Google-Maps-style parcel explorer for Florida: click (or search) any point to
identify the parcel there, see neighboring parcel boundaries in the viewport,
and pull nearby comparable sales — all from the FL DOR statewide cadastral layer
we already use for single-parcel lookups. No geocoding cost (coordinates come
from the click), so these are cheap; still gated behind auth to keep the
upstream ArcGIS service from being hammered by anonymous traffic.
"""
import math
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from app.collectors.parcel_fl import get_parcel_data, URL as _PARCEL_URL, _CO_NO_TO_COUNTY
from app.collectors.geocodio import geocode as _geocode
from app.routers.batch import _user_id_from_token, _AuthUnavailable, _screen_coordinate

router = APIRouter(prefix="/api/explore")

# Largest viewport we'll draw parcel boundaries for (~0.06 deg ≈ 6.5km). Beyond
# this the parcel count explodes and the draw is useless anyway, so the frontend
# only queries when zoomed in and we hard-stop oversized boxes here too.
_MAX_BBOX_DEG = 0.06
_BBOX_CAP = 500          # max parcels drawn per viewport
_COMPS_RADIUS_DEG = 0.013  # ~1.4km search box — small enough to stay fast even
                           # in dense metros (a wider box makes the ArcGIS sort
                           # time out), big enough for a good comp set.
_COMPS_FETCH = 40          # fetch this many, then filter/sort to the best few
_COMPS_RETURN = 12


async def _require_auth(authorization: Optional[str]) -> Optional[JSONResponse]:
    """401 unless a valid Supabase token is present. Fail-open only if the
    verification infrastructure itself is unavailable (same policy as screening)."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    try:
        ident = await _user_id_from_token(token)
    except _AuthUnavailable as e:
        print(f"[Explore] auth verification unavailable ({e}) — allowing")
        return None
    if not ident:
        return JSONResponse(status_code=401, content={"error": "Sign in to use map exploration."})
    return None


def _lu_category(code) -> str:
    """FL DOR use code -> broad category. Mirrors the frontend's
    getLandUseCategory so comps can be matched to the subject's category."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "Other"
    if c in (0, 9, 10, 40, 70):
        return "Vacant Land"
    if 1 <= c <= 8:
        return "Residential"
    if c == 12:
        return "Mixed Use"
    if 11 <= c <= 39:
        return "Commercial"
    if 41 <= c <= 49:
        return "Industrial"
    if 50 <= c <= 69:
        return "Agricultural"
    if 80 <= c <= 89:
        return "Government/Conservation"
    return "Other"


def _rings_to_latlng(rings) -> list:
    """esri rings (with outSR=4326) come back as [x=lng, y=lat]; Leaflet wants
    [lat, lng]. Returns just the first (outer) ring — enough to draw the parcel."""
    if not rings:
        return []
    outer = rings[0]
    out = []
    for pt in outer:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            out.append([pt[1], pt[0]])
    return out


def _ring_centroid(latlng_ring) -> Optional[list]:
    if not latlng_ring:
        return None
    sa = sn = 0.0
    n = 0
    for p in latlng_ring:
        sa += p[0]; sn += p[1]; n += 1
    return [sa / n, sn / n] if n else None


def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


@router.get("/parcel")
async def parcel_at(lat: float, lng: float, authorization: Optional[str] = Header(None)):
    """Identify the parcel at a clicked point (full attributes + boundary)."""
    denied = await _require_auth(authorization)
    if denied is not None:
        return denied
    data = await get_parcel_data(lat, lng)
    if data.get("found"):
        data["land_use_category"] = _lu_category(data.get("land_use_code"))
        data["click_lat"] = lat
        data["click_lng"] = lng
    return data


@router.get("/screen")
async def screen_point(lat: float, lng: float, authorization: Optional[str] = Header(None)):
    """Run the full ParcelIQ screening on a clicked coordinate (no geocoding) —
    returns the verdict, score, signals, and enriched parcel_info, same as the
    Deal Review. Slower than /parcel (runs every data collector), so the UI
    fetches it after showing the quick facts."""
    denied = await _require_auth(authorization)
    if denied is not None:
        return denied
    try:
        return await _screen_coordinate("", {"lat": lat, "lng": lng}, lat, lng)
    except Exception as ex:
        print(f"[Explore] screen error: {ex}")
        return {"verdict": "ERROR", "error": "screen_failed"}


@router.get("/geocode")
async def geocode_q(q: str = Query(...), authorization: Optional[str] = Header(None)):
    """Geocode a search-bar query to a point, so the map can pan there."""
    denied = await _require_auth(authorization)
    if denied is not None:
        return denied
    g = await _geocode(q)
    if g.get("status") == "ok" and g.get("lat") is not None:
        return {"found": True, "lat": g["lat"], "lng": g["lng"], "formatted": g.get("formatted_address")}
    return {"found": False, "status": g.get("status")}


@router.get("/parcels")
async def parcels_in_bbox(
    w: float = Query(...), s: float = Query(...), e: float = Query(...), n: float = Query(...),
    authorization: Optional[str] = Header(None),
):
    """All parcel boundaries in a viewport, so the map shows clickable parcels."""
    denied = await _require_auth(authorization)
    if denied is not None:
        return denied
    if (e - w) > _MAX_BBOX_DEG or (n - s) > _MAX_BBOX_DEG or e <= w or n <= s:
        return {"parcels": [], "too_large": True}
    params = {
        "where": "1=1",
        "geometry": f"{w},{s},{e},{n}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PARCEL_ID,OWN_NAME,DOR_UC,JV,LND_SQFOOT,PHY_ADDR1,SALE_PRC1,SALE_YR1",
        "returnGeometry": "true",
        "resultRecordCount": str(_BBOX_CAP),
        "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.get(_PARCEL_URL, params=params)
            r.raise_for_status()
            data = r.json()
        if "error" in data:
            return {"parcels": [], "error": "upstream"}
        out = []
        for feat in data.get("features", []):
            attrs = feat.get("attributes", {})
            ring = _rings_to_latlng((feat.get("geometry") or {}).get("rings"))
            if len(ring) < 3:
                continue
            sq = attrs.get("LND_SQFOOT")
            out.append({
                "parcel_id": attrs.get("PARCEL_ID"),
                "owner": (attrs.get("OWN_NAME") or "").strip() or None,
                "address": (attrs.get("PHY_ADDR1") or "").strip() or None,
                "acreage": round(sq / 43560, 3) if sq else None,
                "land_use_code": str(attrs.get("DOR_UC")) if attrs.get("DOR_UC") is not None else None,
                "just_value": attrs.get("JV"),
                # Last recorded sale — lets the map hover show what each neighbour
                # actually sold for (real comp data on the immediate parcels,
                # not just the sparse recent-sales pins).
                "sale_price": attrs.get("SALE_PRC1"),
                "sale_year": attrs.get("SALE_YR1"),
                "ring": ring,
            })
        return {"parcels": out, "capped": len(out) >= _BBOX_CAP}
    except Exception as ex:
        print(f"[Explore] bbox error: {ex}")
        return {"parcels": [], "error": "fetch_failed"}


@router.get("/comps")
async def comps(
    lat: float = Query(...), lng: float = Query(...),
    land_use: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
):
    """Nearby comparable sales around a point — recent arms-length-ish sales,
    matched to the subject's land-use category when known, nearest first."""
    denied = await _require_auth(authorization)
    if denied is not None:
        return denied
    d = _COMPS_RADIUS_DEG
    w, s, e, n = lng - d, lat - d, lng + d, lat + d
    year_floor = datetime.now(timezone.utc).year - 6
    params = {
        "where": f"SALE_PRC1 > 1000 AND SALE_YR1 >= {year_floor}",
        "geometry": f"{w},{s},{e},{n}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PARCEL_ID,PHY_ADDR1,PHY_CITY,SALE_PRC1,SALE_YR1,LND_SQFOOT,DOR_UC",
        # Centroid only (not full polygons) — ~2x faster and all we need for the
        # distance + the map pin.
        "returnGeometry": "false",
        "returnCentroid": "true",
        "orderByFields": "SALE_YR1 DESC",
        "resultRecordCount": str(_COMPS_FETCH),
        "f": "json",
    }
    subj_cat = _lu_category(land_use) if land_use is not None else None
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.get(_PARCEL_URL, params=params)
            r.raise_for_status()
            data = r.json()
        if "error" in data:
            return {"comps": [], "error": "upstream"}
        rows = []
        for feat in data.get("features", []):
            a = feat.get("attributes", {})
            c = feat.get("centroid") or {}
            if c.get("y") is None or c.get("x") is None:
                continue
            cen = [c["y"], c["x"]]
            sq = a.get("LND_SQFOOT")
            acre = round(sq / 43560, 3) if sq else None
            price = a.get("SALE_PRC1")
            cat = _lu_category(a.get("DOR_UC"))
            rows.append({
                "parcel_id": a.get("PARCEL_ID"),
                "address": (a.get("PHY_ADDR1") or "").strip() or None,
                "city": (a.get("PHY_CITY") or "").strip() or None,
                "sale_price": price,
                "sale_year": a.get("SALE_YR1"),
                "acreage": acre,
                "price_per_acre": round(price / acre) if (price and acre and acre > 0) else None,
                "land_use_category": cat,
                "lat": cen[0], "lng": cen[1],
                "distance_m": round(_haversine_m(lat, lng, cen[0], cen[1])),
            })
        # Prefer same-category comps; fall back to all if too few.
        if subj_cat:
            same = [c for c in rows if c["land_use_category"] == subj_cat]
            rows = same if len(same) >= 3 else rows
        rows.sort(key=lambda c: c["distance_m"])
        return {"comps": rows[:_COMPS_RETURN], "category": subj_cat}
    except Exception as ex:
        print(f"[Explore] comps error: {ex}")
        return {"comps": [], "error": "fetch_failed"}
