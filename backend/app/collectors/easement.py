"""Conservation easements from local PostGIS (fl_easements).

Easements are an AUTO-KILL trigger. Formerly a live ArcGIS point query per
parcel; now a single PostGIS RPC (easement_near) called with the anon key over
PostgREST — same data (FL Conservation Easements), same point-in-polygon
(ST_Intersects), same program-name fallback (MANAME, else ESMT_HOLD). Return
shape unchanged so insight_engine's kill logic is untouched.
"""
import os

import httpx

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY", "")
_RPC = f"{_SUPABASE_URL}/rest/v1/rpc"
_HEADERS = {"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}",
            "Content-Type": "application/json"}

DEFAULT = {"easement_found": False, "easement_type": None, "source": "USDA NRCS / FNAI"}


def _one(payload) -> dict:
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload or {}


async def get_conservation_easement(lat: float, lng: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS) as client:
            resp = await client.post(f"{_RPC}/easement_near", json={"in_lat": lat, "in_lng": lng})
            resp.raise_for_status()
            row = _one(resp.json())
        if not row.get("found"):
            return dict(DEFAULT)
        return {
            "easement_found": True,
            "easement_type": str(row.get("program") or "Conservation easement"),
            "source": "USDA NRCS / FNAI",
        }
    except Exception as e:
        print(f"[Easement] Error: {e}")
        return dict(DEFAULT)
