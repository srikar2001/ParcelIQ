"""Critical habitat from local PostGIS (fl_habitat, USFWS Critical Habitat).

Formerly a live USFWS ArcGIS query per parcel. Now a single PostGIS RPC
(habitat_near) called with the anon key over PostgREST — same data, same ~333m
box (ST_MakeEnvelope replicates the old esriGeometryEnvelope intersect), no
live-API dependency. Return shape unchanged so insight_engine/batch.py are
untouched.
"""
import os

import httpx

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY", "")
_RPC = f"{_SUPABASE_URL}/rest/v1/rpc"
_HEADERS = {"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}",
            "Content-Type": "application/json"}

_CLEAN = {"habitat_found": False, "species": [], "source": "USFWS Critical Habitat", "data_available": True}
_ERROR = {"habitat_found": False, "species": [], "source": "USFWS Critical Habitat", "data_available": False}


def _one(payload) -> dict:
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload or {}


async def get_critical_habitat(lat: float, lng: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS) as client:
            resp = await client.post(f"{_RPC}/habitat_near", json={"in_lat": lat, "in_lng": lng})
            resp.raise_for_status()
            row = _one(resp.json())
        return {
            "habitat_found":  bool(row.get("found")),
            "species":        row.get("species") or [],
            "source":         "USFWS Critical Habitat",
            "data_available": True,
        }
    except Exception as e:
        print(f"[CriticalHabitat] Error: {e}")
        return dict(_ERROR)
