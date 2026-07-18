"""Road access via US Census TIGERweb (ArcGIS).

Road access is the #1 kill signal land investors check — it must be reliable.
Overpass (OSM) rate-limits to ~2 concurrent per IP and collapses under batch
load, which is why road data used to fail on most batch rows. TIGERweb is a
government ArcGIS service with no such limits (~0.3s/query), covers every
public road in the US, and returns road class + name + geometry so we can
report the actual distance from the parcel to the nearest road.
"""
import math

import httpx

_BASE = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer"
_LAYERS = [8, 6, 2]          # local roads first, then secondary, then primary
SEARCH_RADIUS_M = 150
# Rural / large-lot parcels geocode to a point that can sit well past 150m from
# the frontage road (e.g. "5510 Paces Landing Rd" — its own road is ~400m from
# the geocoded point). Searching only 150m reported those as "no road access",
# which is wrong. Try 150m first (keeps urban precision), then widen until a
# road is found. Truly landlocked parcels (nothing within 800m) still return
# NONE_FOUND. The reported road_distance_m makes a far road self-evident.
_SEARCH_RADII_M = [150, 400, 800]

# MTFCC road classification -> (road_type, surface implication)
_MTFCC = {
    "S1100": ("primary",     "paved"),
    "S1200": ("secondary",   "paved"),
    "S1400": ("residential", "unknown"),   # local street — surface not recorded
    "S1500": ("track",       "dirt"),      # 4WD vehicular trail
    "S1630": ("ramp",        "paved"),
    "S1640": ("service",     "unknown"),
    "S1740": ("private",     "unknown"),   # private road / service drive
    "S1750": ("driveway",    "unknown"),
    "S1780": ("service",     "unknown"),   # parking lot road
}
# Walkways, stairs, trails, bike/bridle paths — not vehicle access
_EXCLUDE = {"S1710", "S1720", "S1730", "S1820", "S1830"}
_PRIVATE = {"S1740", "S1750"}

ERROR = {"road_found": None, "road_surface": "error", "road_type": None,
         "road_name": None, "road_distance_m": None, "road_private": None,
         "road_nearest_lat": None, "road_nearest_lng": None,
         "source": "US Census TIGER"}
NONE_FOUND = {"road_found": False, "road_surface": "none", "road_type": None,
              "road_name": None, "road_distance_m": None, "road_private": None,
              "road_nearest_lat": None, "road_nearest_lng": None,
              "source": "US Census TIGER"}


def _dist_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = (lat2 - lat1) * 111_320.0
    dlng = (lng2 - lng1) * 111_320.0 * math.cos(math.radians(lat1))
    return math.hypot(dlat, dlng)


def _min_distance(lat: float, lng: float, paths: list) -> tuple[float, float, float] | None:
    """Returns (distance_m, nearest_lat, nearest_lng) for the closest vertex
    across all paths, or None if no path has a usable point. The frontend
    draws a real line to this point, so it must be an actual road vertex —
    not an interpolated or approximate position."""
    best = None
    best_pt = None
    for path in paths or []:
        for pt in path:
            if len(pt) < 2:
                continue
            plng, plat = pt[0], pt[1]  # ArcGIS paths are [x=lng, y=lat]
            d = _dist_m(lat, lng, plat, plng)
            if best is None or d < best:
                best = d
                best_pt = (plat, plng)
    if best is None or best_pt is None:
        return None
    return best, best_pt[0], best_pt[1]


async def _query_radius(client: httpx.AsyncClient, lat: float, lng: float,
                        radius_m: float) -> dict | None:
    """Search all layers within radius_m. Returns a road result dict, or None
    if no vehicle road was found at this radius (caller widens and retries)."""
    dlat = radius_m / 111_320.0
    dlng = radius_m / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    envelope = f"{lng - dlng},{lat - dlat},{lng + dlng},{lat + dlat}"
    params = {
        "geometry": envelope,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "NAME,MTFCC",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
    }
    for layer in _LAYERS:
        resp = await client.get(f"{_BASE}/{layer}/query", params=params)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"TIGERweb error: {data['error']}")
        feats = [f for f in data.get("features", [])
                 if (f.get("attributes", {}).get("MTFCC") or "") not in _EXCLUDE]
        if not feats:
            continue

        scored = []
        for f in feats:
            attrs = f.get("attributes", {})
            mtfcc = attrs.get("MTFCC") or ""
            result = _min_distance(lat, lng, (f.get("geometry") or {}).get("paths"))
            dist, nlat, nlng = result if result is not None else (1e9, None, None)
            scored.append((mtfcc in _PRIVATE, dist, nlat, nlng, mtfcc, attrs.get("NAME")))
        # Prefer public roads over private drives, then nearest
        scored.sort(key=lambda t: (t[0], t[1]))
        is_private, dist, nlat, nlng, mtfcc, name = scored[0]
        road_type, surface = _MTFCC.get(mtfcc, ("road", "unknown"))
        return {
            "road_found": True,
            "road_type": road_type,
            "road_surface": surface,
            "road_name": (name or "").strip() or None,
            "road_distance_m": round(dist) if dist < 1e9 else None,
            "road_private": bool(is_private),
            "road_nearest_lat": nlat if dist < 1e9 else None,
            "road_nearest_lng": nlng if dist < 1e9 else None,
            "source": "US Census TIGER",
        }
    return None


async def get_road_access(lat: float, lng: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            for radius_m in _SEARCH_RADII_M:
                found = await _query_radius(client, lat, lng, radius_m)
                if found is not None:
                    return found
        return dict(NONE_FOUND)
    except Exception as e:
        print(f"[RoadsTIGER] Error: {e}")
        return dict(ERROR)
