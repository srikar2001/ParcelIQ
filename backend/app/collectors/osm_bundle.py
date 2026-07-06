"""Combined OpenStreetMap collector — roads, waterways, and powerlines in ONE
Overpass query per parcel.

Overpass allows ~2 concurrent requests per IP. The old design issued 3-4
separate Overpass calls per parcel (roads, waterways, powerlines x2), so a
20-parcel chunk fired ~60+ concurrent requests and got blanket 429s — road
data failed on nearly every batch row. One union query per parcel keeps the
shared Semaphore(2) viable at batch scale.
"""
import asyncio
import time

import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "ParcelIQ/1.0", "Accept": "*/*"}

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

PAVED = {"paved", "asphalt", "concrete", "cobblestone"}
DIRT  = {"unpaved", "dirt", "gravel", "ground", "grass", "sand", "compacted"}

ROADS_ERROR      = {"road_found": False, "road_surface": "error", "road_type": None, "source": "OpenStreetMap"}
ROADS_NONE       = {"road_found": False, "road_surface": "none",  "road_type": None, "source": "OpenStreetMap"}
WATERWAYS_NONE   = {"waterway_nearby": False, "waterway_type": None, "distance_approx": None, "source": "OpenStreetMap"}
POWERLINES_ERROR = {"powerline_nearby": None, "powerline_distance": None, "source": "OpenStreetMap"}

BUNDLE_ERROR = {"_error": True, "roads": ROADS_ERROR, "waterways": WATERWAYS_NONE, "powerlines": POWERLINES_ERROR}


def _parse_roads(elements: list) -> dict:
    if not elements:
        return ROADS_NONE
    best = elements[0]
    for el in elements:
        hw = el.get("tags", {}).get("highway", "")
        if hw in ("residential", "primary", "secondary", "tertiary", "unclassified"):
            best = el
            break
    tags = best.get("tags", {})
    surface = tags.get("surface", "").lower()
    if surface in PAVED:
        road_surface = "paved"
    elif surface in DIRT:
        road_surface = "dirt"
    else:
        road_surface = "unknown"
    return {
        "road_found": True,
        "road_surface": road_surface,
        "road_type": tags.get("highway"),
        "road_name": tags.get("name"),
        "road_access": tags.get("access"),
        "source": "OpenStreetMap",
    }


def _parse_waterways(elements: list) -> dict:
    if not elements:
        return WATERWAYS_NONE
    waterway_types = [el.get("tags", {}).get("waterway", "") for el in elements]
    priority = ["canal", "river", "stream", "drain", "ditch"]
    best_type = "waterway"
    for p in priority:
        if p in waterway_types:
            best_type = p
            break
    return {
        "waterway_nearby": True,
        "waterway_type": best_type,
        "distance_approx": "< 200m",
        "source": "OpenStreetMap",
    }


async def get_osm_bundle(lat: float, lng: float) -> dict:
    """Returns {"_error": bool, "roads": {...}, "waterways": {...}, "powerlines": {...}}.

    Caller is expected to hold the shared Overpass semaphore for the whole call
    (including the rare 1-mile powerline follow-up query).
    """
    try:
        query = f"""[out:json][timeout:12];
(
  way(around:100,{lat},{lng})[highway];
  way(around:200,{lat},{lng})[waterway];
  relation(around:200,{lat},{lng})[waterway];
  way(around:500,{lat},{lng})[power~"^(line|minor_line|cable)$"];
  node(around:500,{lat},{lng})[power~"^(tower|pole)$"];
);
out tags;"""
        elements = (await _op_post_retry(query)).get("elements", [])

        highways  = [el for el in elements if "highway"  in el.get("tags", {})]
        waterways = [el for el in elements if "waterway" in el.get("tags", {})]
        power     = [el for el in elements if "power"    in el.get("tags", {})]

        if power:
            powerlines = {"powerline_nearby": True, "powerline_distance": "< 500m", "source": "OpenStreetMap"}
        else:
            # Nothing within 500m — check out to 1 mile with a cheap count query
            q1600 = f"""[out:json][timeout:12];
(way(around:1600,{lat},{lng})[power~"^(line|minor_line)$"];);
out count;"""
            try:
                data2 = await _op_post_retry(q1600)
                count_el = next((e for e in data2.get("elements", []) if e.get("type") == "count"), None)
                total = int(count_el.get("tags", {}).get("total", 0)) if count_el else 0
                if total > 0:
                    powerlines = {"powerline_nearby": True, "powerline_distance": "< 1 mile", "source": "OpenStreetMap"}
                else:
                    powerlines = {"powerline_nearby": False, "powerline_distance": "> 1 mile", "source": "OpenStreetMap"}
            except Exception as e:
                print(f"[OSMBundle] Powerline follow-up error: {e}")
                powerlines = POWERLINES_ERROR

        return {
            "_error": False,
            "roads": _parse_roads(highways),
            "waterways": _parse_waterways(waterways),
            "powerlines": powerlines,
        }
    except Exception as e:
        print(f"[OSMBundle] Error: {e}")
        return BUNDLE_ERROR
