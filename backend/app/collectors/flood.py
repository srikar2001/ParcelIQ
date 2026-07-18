"""Flood zone from local PostGIS (fl_flood_zones, FEMA NFHL data).

Formerly a live FEMA NFHL ArcGIS point query per parcel; now a single PostGIS
RPC (flood_zone_at) called with the anon key over PostgREST — same FEMA data
(all Florida S_FLD_HAZ_AR polygons), same point-in-polygon, same zone/SFHA/BFE
derivation. Geometry is generalized ~10m (finer than geocoding error) so the
zone a point falls in is unchanged. Return shape unchanged so insight_engine's
flood kill logic is untouched.
"""
import os

import httpx

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY", "")
_RPC = f"{_SUPABASE_URL}/rest/v1/rpc"
_HEADERS = {"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}",
            "Content-Type": "application/json"}

DEFAULT    = {"zone": "UNKNOWN",    "sfha": False, "source": "FEMA NFHL"}
NOT_MAPPED = {"zone": "NOT_MAPPED", "sfha": False, "source": "FEMA NFHL"}
API_ERROR  = {"zone": "ERROR",      "sfha": False, "source": "FEMA NFHL"}

_SFHA_ZONES = {"AE", "VE", "V", "AO", "AH", "A"}


async def get_flood_zone(lat: float, lng: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS) as client:
            resp = await client.post(f"{_RPC}/flood_zone_at", json={"in_lat": lat, "in_lng": lng})
            resp.raise_for_status()
            rows = resp.json()
        if not rows:                       # no flood polygon contains the point
            return dict(NOT_MAPPED)
        row = rows[0] if isinstance(rows, list) else rows

        zone = (row.get("fld_zone") or "").strip().upper() or "UNKNOWN"

        sfha_raw = row.get("sfha_tf")
        if sfha_raw is None:
            sfha = zone in _SFHA_ZONES
        else:
            sfha = str(sfha_raw).strip().upper() == "T"

        bfe_raw = row.get("static_bfe")
        bfe = float(bfe_raw) if bfe_raw not in (None, -9999, -9999.0) else None

        return {
            "zone": zone,
            "zone_subtype": (row.get("zone_subty") or "").strip(),
            "sfha": sfha,
            "base_flood_elevation_ft": bfe,
            "source": "FEMA NFHL",
        }
    except Exception as e:
        print(f"[Flood] Error: {e}")
        return dict(API_ERROR)
