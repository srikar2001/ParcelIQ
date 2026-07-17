"""Wetlands from local PostGIS (fl_wetlands, USFWS NWI data).

Formerly a live USFWS NWI ArcGIS query per parcel. Now a single PostGIS RPC
(wetland_near) called with the anon key over PostgREST — same NWI data, same
~50m "on parcel" / ~111m "nearby" boxes (ST_MakeEnvelope replicates the old
esriGeometryEnvelope intersect exactly), no live-API dependency.

Return shape is unchanged so insight_engine + batch.py need no edits.
"""
import os

import httpx

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY", "")
_RPC = f"{_SUPABASE_URL}/rest/v1/rpc"
_HEADERS = {"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}",
            "Content-Type": "application/json"}

DEFAULT = {"wetland_on_parcel": False, "wetland_nearby": False, "wetland_type": None, "wetland_code": None, "source": "USFWS NWI"}
ERROR   = {"wetland_on_parcel": None,  "wetland_nearby": None,  "wetland_type": None, "wetland_code": None, "source": "USFWS NWI", "error": True}


def _one(payload) -> dict:
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload or {}


async def get_wetlands(lat: float, lng: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS) as client:
            resp = await client.post(f"{_RPC}/wetland_near", json={"in_lat": lat, "in_lng": lng})
            resp.raise_for_status()
            row = _one(resp.json())
        return {
            "wetland_on_parcel": bool(row.get("on_parcel")),
            "wetland_nearby":    bool(row.get("nearby")),
            "wetland_type":      row.get("wetland_type"),
            "wetland_code":      row.get("wetland_code"),
            "source":            "USFWS NWI",
        }
    except Exception as e:
        print(f"[Wetlands] Error: {e}")
        return dict(ERROR)
