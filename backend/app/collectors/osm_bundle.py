"""Waterways + powerlines from local PostGIS (fl_waterways / fl_powerlines).

Formerly a single rate-limited Overpass query per parcel — Overpass allows
only ~2 concurrent requests per IP and collapsed under batch load, which was
the dominant cause of large batches stalling. These two signals now come from
local Supabase tables (loaded from the same OpenStreetMap data) via PostGIS
RPC functions, called with the existing anon key over PostgREST. No rate
limit, no semaphore, no backoff. Road access lives in roads_tiger.py.

Return shape is unchanged so callers (batch.py) need no edits:
  {"_error": bool, "waterways": {...}, "powerlines": {...}}
"""
import os

import httpx

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY", "")
_RPC = f"{_SUPABASE_URL}/rest/v1/rpc"
_HEADERS = {
    "apikey": _SUPABASE_KEY,
    "Authorization": f"Bearer {_SUPABASE_KEY}",
    "Content-Type": "application/json",
}

WATERWAYS_NONE   = {"waterway_nearby": False, "waterway_type": None, "distance_approx": None,
                    "waterway_nearest_lat": None, "waterway_nearest_lng": None, "source": "OpenStreetMap"}
POWERLINES_ERROR = {"powerline_nearby": None, "powerline_distance": None,
                    "powerline_nearest_lat": None, "powerline_nearest_lng": None, "source": "OpenStreetMap"}

BUNDLE_ERROR = {"_error": True, "waterways": WATERWAYS_NONE, "powerlines": POWERLINES_ERROR}


def _one(payload) -> dict:
    """PostgREST returns a set-returning function as a JSON array of rows."""
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload or {}


async def get_osm_bundle(lat: float, lng: float) -> dict:
    """Returns {"_error": bool, "waterways": {...}, "powerlines": {...}}."""
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS) as client:
            wr, pr = None, None
            resp = await client.post(f"{_RPC}/waterway_near", json={"in_lat": lat, "in_lng": lng})
            resp.raise_for_status()
            wr = _one(resp.json())
            resp = await client.post(f"{_RPC}/power_near", json={"in_lat": lat, "in_lng": lng})
            resp.raise_for_status()
            pr = _one(resp.json())

        if wr.get("nearby"):
            waterways = {
                "waterway_nearby": True,
                "waterway_type": wr.get("waterway_type") or "waterway",
                "distance_approx": "< 200m",
                "waterway_nearest_lat": wr.get("near_lat"),
                "waterway_nearest_lng": wr.get("near_lng"),
                "source": "OpenStreetMap",
            }
        else:
            waterways = dict(WATERWAYS_NONE)

        if pr.get("within_500"):
            powerlines = {
                "powerline_nearby": True,
                "powerline_distance": "< 500m",
                "powerline_nearest_lat": pr.get("near_lat"),
                "powerline_nearest_lng": pr.get("near_lng"),
                "source": "OpenStreetMap",
            }
        elif pr.get("within_1600"):
            powerlines = {
                "powerline_nearby": True,
                "powerline_distance": "< 1 mile",
                "powerline_nearest_lat": None,
                "powerline_nearest_lng": None,
                "source": "OpenStreetMap",
            }
        else:
            powerlines = {
                "powerline_nearby": False,
                "powerline_distance": "> 1 mile",
                "powerline_nearest_lat": None,
                "powerline_nearest_lng": None,
                "source": "OpenStreetMap",
            }

        return {"_error": False, "waterways": waterways, "powerlines": powerlines}
    except Exception as e:
        print(f"[OSMBundle] Error: {e}")
        return {"_error": True, "waterways": dict(WATERWAYS_NONE), "powerlines": dict(POWERLINES_ERROR)}
