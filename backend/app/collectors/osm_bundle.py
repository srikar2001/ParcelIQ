"""Combined OpenStreetMap collector — waterways and powerlines in ONE
Overpass query per parcel.

Overpass allows ~2 concurrent requests per IP, so everything that still
depends on it is bundled into a single query. Road access moved to the
Census TIGERweb collector (roads_tiger.py) — it is the #1 kill signal and
could not be left on a host this rate-limited.
"""
import asyncio
import math
import time

import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "ParcelIQ/1.0", "Accept": "*/*"}


def _dist_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = (lat2 - lat1) * 111_320.0
    dlng = (lng2 - lng1) * 111_320.0 * math.cos(math.radians(lat1))
    return math.hypot(dlat, dlng)


def _element_points(el: dict) -> list[tuple[float, float]]:
    """(lat, lng) points for one Overpass `out geom` element — a node has a
    single point, a way carries its point list under "geometry"."""
    if el.get("type") == "node" and el.get("lat") is not None and el.get("lon") is not None:
        return [(el["lat"], el["lon"])]
    return [(pt["lat"], pt["lon"]) for pt in el.get("geometry") or [] if pt.get("lat") is not None]


def _nearest_point(lat: float, lng: float, elements: list) -> tuple[float, float, float] | None:
    """Nearest real vertex across a set of elements — the frontend draws a
    line straight to this point, so (like roads_tiger) it must be an actual
    OSM vertex, never an interpolated or averaged position."""
    best = None
    best_pt = None
    for el in elements:
        for plat, plng in _element_points(el):
            d = _dist_m(lat, lng, plat, plng)
            if best is None or d < best:
                best = d
                best_pt = (plat, plng)
    if best is None or best_pt is None:
        return None
    return best, best_pt[0], best_pt[1]

# Overpass gives 2 slots per IP, and each slot has a cooldown after every
# query — firing back-to-back even at 2 concurrent yields 429/504. Space
# request STARTS globally, and back off hard when the server says busy.
_MIN_GAP = 0.7          # seconds between request starts (global)
_BUSY_BACKOFF = 6.0     # seconds to wait after a 429/504 before retrying
_gap_lock = asyncio.Lock()
_last_start = 0.0


class _OverpassBusy(Exception):
    pass


async def _op_post(query: str) -> dict:
    """POST one Overpass query with global start-spacing. Raises _OverpassBusy
    on 429/504 so callers can back off and retry."""
    global _last_start
    async with _gap_lock:
        wait = _MIN_GAP - (time.monotonic() - _last_start)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_start = time.monotonic()
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
        resp = await client.post(OVERPASS_URL, data={"data": query})
        if resp.status_code in (429, 504):
            raise _OverpassBusy(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        return resp.json()


async def _op_post_retry(query: str) -> dict:
    try:
        return await _op_post(query)
    except _OverpassBusy:
        await asyncio.sleep(_BUSY_BACKOFF)
        return await _op_post(query)


WATERWAYS_NONE   = {"waterway_nearby": False, "waterway_type": None, "distance_approx": None,
                     "waterway_nearest_lat": None, "waterway_nearest_lng": None, "source": "OpenStreetMap"}
POWERLINES_ERROR = {"powerline_nearby": None, "powerline_distance": None,
                     "powerline_nearest_lat": None, "powerline_nearest_lng": None, "source": "OpenStreetMap"}

BUNDLE_ERROR = {"_error": True, "waterways": WATERWAYS_NONE, "powerlines": POWERLINES_ERROR}


def _parse_waterways(lat: float, lng: float, elements: list) -> dict:
    if not elements:
        return WATERWAYS_NONE
    waterway_types = [el.get("tags", {}).get("waterway", "") for el in elements]
    priority = ["canal", "river", "stream", "drain", "ditch"]
    best_type = "waterway"
    for p in priority:
        if p in waterway_types:
            best_type = p
            break
    nearest = _nearest_point(lat, lng, elements)
    return {
        "waterway_nearby": True,
        "waterway_type": best_type,
        "distance_approx": "< 200m",
        "waterway_nearest_lat": nearest[1] if nearest else None,
        "waterway_nearest_lng": nearest[2] if nearest else None,
        "source": "OpenStreetMap",
    }


async def get_osm_bundle(lat: float, lng: float) -> dict:
    """Returns {"_error": bool, "waterways": {...}, "powerlines": {...}}.

    Caller is expected to hold the shared Overpass semaphore for the whole call
    (including the rare 1-mile powerline follow-up query).
    """
    try:
        query = f"""[out:json][timeout:12];
(
  way(around:200,{lat},{lng})[waterway];
  relation(around:200,{lat},{lng})[waterway];
  way(around:500,{lat},{lng})[power~"^(line|minor_line|cable)$"];
  node(around:500,{lat},{lng})[power~"^(tower|pole)$"];
);
out geom;"""
        elements = (await _op_post_retry(query)).get("elements", [])

        waterways = [el for el in elements if "waterway" in el.get("tags", {})]
        power     = [el for el in elements if "power"    in el.get("tags", {})]

        if power:
            nearest = _nearest_point(lat, lng, power)
            powerlines = {"powerline_nearby": True, "powerline_distance": "< 500m",
                          "powerline_nearest_lat": nearest[1] if nearest else None,
                          "powerline_nearest_lng": nearest[2] if nearest else None,
                          "source": "OpenStreetMap"}
        else:
            # Nothing within 500m — check out to 1 mile with a cheap count query
            q1600 = f"""[out:json][timeout:12];
(way(around:1600,{lat},{lng})[power~"^(line|minor_line)$"];);
out count;"""
            try:
                data2 = await _op_post_retry(q1600)
                count_el = next((e for e in data2.get("elements", []) if e.get("type") == "count"), None)
                total = int(count_el.get("tags", {}).get("total", 0)) if count_el else 0
                # Only a count query — existence is confirmed but no geometry
                # was fetched, so there is genuinely no real point to draw a
                # line to; nearest_lat/lng stay None rather than a guess.
                if total > 0:
                    powerlines = {"powerline_nearby": True, "powerline_distance": "< 1 mile",
                                  "powerline_nearest_lat": None, "powerline_nearest_lng": None,
                                  "source": "OpenStreetMap"}
                else:
                    powerlines = {"powerline_nearby": False, "powerline_distance": "> 1 mile",
                                  "powerline_nearest_lat": None, "powerline_nearest_lng": None,
                                  "source": "OpenStreetMap"}
            except Exception as e:
                print(f"[OSMBundle] Powerline follow-up error: {e}")
                powerlines = POWERLINES_ERROR

        return {
            "_error": False,
            "waterways": _parse_waterways(lat, lng, waterways),
            "powerlines": powerlines,
        }
    except Exception as e:
        print(f"[OSMBundle] Error: {e}")
        return BUNDLE_ERROR
