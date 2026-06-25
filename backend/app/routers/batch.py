import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.collectors.geocodio import geocode
from app.collectors.flood import get_flood_zone
from app.collectors.wetlands import get_wetlands
from app.collectors.critical_habitat import get_critical_habitat
from app.collectors.epa import get_epa_superfund
from app.collectors.roads_osm import get_road_type
from app.collectors.parcel_fl import get_parcel_data
from app.collectors.waterways import get_waterway_proximity
from app.core.insight_engine import score_parcel
from app.core.cache import get_cached_result, save_cached_result

router = APIRouter(prefix="/api")

BATCH_SIZE   = 10
WEEKLY_LIMIT = 1000
_SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
_SUPABASE_KEY = os.environ.get('SUPABASE_ANON_KEY', '')


async def _user_id_from_token(token: str) -> Optional[str]:
    if not token or not _SUPABASE_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f'{_SUPABASE_URL}/auth/v1/user',
                headers={'Authorization': f'Bearer {token}', 'apikey': _SUPABASE_KEY},
            )
        return r.json().get('id') if r.status_code == 200 else None
    except Exception:
        return None


async def _weekly_usage(user_id: str) -> int:
    if not user_id or not _SUPABASE_URL:
        return 0
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f'{_SUPABASE_URL}/rest/v1/parcel_results',
                params={'select': 'id', 'user_id': f'eq.{user_id}', 'created_at': f'gte.{since}'},
                headers={
                    'apikey': _SUPABASE_KEY,
                    'Authorization': f'Bearer {_SUPABASE_KEY}',
                    'Prefer': 'count=exact',
                    'Range': '0-0',
                },
            )
        cr = r.headers.get('content-range', '')
        return int(cr.split('/')[-1]) if '/' in cr else 0
    except Exception:
        return 0

_FLOOD_DEFAULT     = {"zone": "UNKNOWN", "sfha": False, "source": "FEMA NFHL"}
_WETLANDS_DEFAULT  = {"wetland_on_parcel": False, "wetland_nearby": False, "wetland_type": None, "wetland_code": None, "source": "USFWS NWI"}
_HABITAT_DEFAULT   = {"habitat_found": False, "species": [], "source": "USFWS Critical Habitat"}
_EPA_DEFAULT       = {"contamination_found": False, "sites": [], "source": "EPA ECHO Superfund"}
_ROADS_DEFAULT     = {"road_found": False, "road_surface": "none", "road_type": None, "source": "OpenStreetMap"}
_PARCEL_DEFAULT    = {"found": False, "source": "FL DOR Cadastral", "geometry": []}
_WATERWAYS_DEFAULT = {"waterway_nearby": False, "waterway_type": None, "source": "OpenStreetMap"}

# In-memory job store for large batches (Task 30)
_jobs: dict = {}


class ParcelInput(BaseModel):
    address: str
    apn: Optional[str] = None


class BatchRequest(BaseModel):
    parcels: list[ParcelInput]
    state: str = "FL"


async def _safe(coro, default):
    try:
        return await coro
    except Exception:
        return default


async def _process_parcel(parcel: ParcelInput) -> dict:
    address = parcel.address

    # Check cache first (Task 28)
    cached = await get_cached_result(address)
    if cached:
        cached["cached"] = True
        return cached

    geo = await geocode(address)
    if geo is None:
        return {
            "address": address,
            "verdict": "ERROR",
            "score": None,
            "auto_kill": False,
            "auto_kill_reason": None,
            "flags": [],
            "positives": [],
            "parcel_info": {},
            "sources": [],
            "error": "Geocoding failed — address not found",
            "cached": False,
        }

    lat, lng = geo["lat"], geo["lng"]

    flood, wetlands, habitat, epa, roads, parcel_data, waterways = await asyncio.gather(
        _safe(get_flood_zone(lat, lng), _FLOOD_DEFAULT),
        _safe(get_wetlands(lat, lng), _WETLANDS_DEFAULT),
        _safe(get_critical_habitat(lat, lng), _HABITAT_DEFAULT),
        _safe(get_epa_superfund(lat, lng), _EPA_DEFAULT),
        _safe(get_road_type(lat, lng), _ROADS_DEFAULT),
        _safe(get_parcel_data(lat, lng), _PARCEL_DEFAULT),
        _safe(get_waterway_proximity(lat, lng), _WATERWAYS_DEFAULT),
    )

    result = score_parcel(flood, wetlands, habitat, epa, roads, parcel_data, waterways)

    out = {
        "address": geo.get("formatted_address", address),
        "verdict": result["verdict"],
        "score": result["score"],
        "auto_kill": result["auto_kill"],
        "auto_kill_reason": result["auto_kill_reason"],
        "flags": result["flags"],
        "positives": result["positives"],
        "parcel_info": {
            "acreage": parcel_data.get("acreage"),
            "owner": parcel_data.get("owner"),
            "land_use_code": parcel_data.get("land_use_code"),
            "last_sale_price": parcel_data.get("last_sale_price"),
            "geometry": parcel_data.get("geometry", []),
        },
        "_lat": lat,
        "_lng": lng,
        "sources": result["sources_checked"],
        "error": None,
        "cached": False,
    }

    # Save to cache (fire and forget)
    asyncio.create_task(save_cached_result(address, out))
    return out


async def _process_parcel_safe(parcel: ParcelInput) -> dict:
    try:
        return await _process_parcel(parcel)
    except Exception as e:
        return {
            "address": parcel.address,
            "verdict": "ERROR",
            "score": None,
            "auto_kill": False,
            "auto_kill_reason": None,
            "flags": [],
            "positives": [],
            "parcel_info": {},
            "sources": [],
            "error": str(e),
        }


@router.post("/batch/stream")
async def batch_screen_stream(request: BatchRequest):
    async def event_generator():
        parcels = request.parcels
        total = len(parcels)
        all_results = []
        completed = 0

        yield f'data: {json.dumps({"type":"start","total":total})}\n\n'

        for i in range(0, total, BATCH_SIZE):
            chunk = parcels[i:i + BATCH_SIZE]
            chunk_results = await asyncio.gather(
                *[_process_parcel_safe(p) for p in chunk]
            )
            for result in chunk_results:
                all_results.append(result)
                completed += 1
                pct = int((completed / total) * 100)
                yield f'data: {json.dumps({"type":"progress","completed":completed,"total":total,"percent":pct,"latest":result})}\n\n'

        summary = {
            "total":   len(all_results),
            "kill":    sum(1 for r in all_results if r["verdict"] == "KILL"),
            "review":  sum(1 for r in all_results if r["verdict"] == "REVIEW"),
            "pursue":  sum(1 for r in all_results if r["verdict"] == "PURSUE"),
            "error":   sum(1 for r in all_results if r["verdict"] == "ERROR"),
        }
        yield f'data: {json.dumps({"type":"complete","summary":summary,"results":all_results})}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/batch")
async def batch_screen(
    request: BatchRequest,
    authorization: Optional[str] = Header(None),
):
    if authorization:
        token   = authorization.removeprefix('Bearer ').strip()
        user_id = await _user_id_from_token(token)
        if user_id:
            used = await _weekly_usage(user_id)
            if used + len(request.parcels) > WEEKLY_LIMIT:
                return JSONResponse(
                    status_code=429,
                    content={
                        'limit_exceeded': True,
                        'used': used,
                        'limit': WEEKLY_LIMIT,
                        'message': 'Weekly screening limit reached',
                    },
                )

    all_results = []
    parcels = request.parcels

    for i in range(0, len(parcels), BATCH_SIZE):
        chunk = parcels[i : i + BATCH_SIZE]
        chunk_results = await asyncio.gather(
            *[_process_parcel_safe(p) for p in chunk]
        )
        all_results.extend(chunk_results)

    summary = {
        "total": len(all_results),
        "pursue": sum(1 for r in all_results if r["verdict"] == "PURSUE"),
        "review": sum(1 for r in all_results if r["verdict"] == "REVIEW"),
        "kill": sum(1 for r in all_results if r["verdict"] == "KILL"),
        "error": sum(1 for r in all_results if r["verdict"] == "ERROR"),
    }

    return {"summary": summary, "results": all_results}


# ── QUEUE for large batches (Task 30)
async def _process_batch_background(job_id: str, parcels: list) -> None:
    _jobs[job_id]["status"] = "processing"
    for i in range(0, len(parcels), BATCH_SIZE):
        chunk = parcels[i:i + BATCH_SIZE]
        results = await asyncio.gather(*[_process_parcel_safe(p) for p in chunk])
        _jobs[job_id]["results"].extend(results)
        _jobs[job_id]["progress"] = len(_jobs[job_id]["results"])
    _jobs[job_id]["status"] = "complete"


@router.post("/batch/queue")
async def batch_queue(request: BatchRequest):
    job_id = str(uuid4())
    _jobs[job_id] = {"status": "queued", "progress": 0, "total": len(request.parcels), "results": [], "error": None}
    asyncio.create_task(_process_batch_background(job_id, request.parcels))
    return {"job_id": job_id, "total": len(request.parcels)}


@router.get("/batch/status/{job_id}")
async def batch_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return {
        "status": job["status"],
        "progress": job["progress"],
        "total": job["total"],
        "results": job["results"] if job["status"] == "complete" else [],
    }


# ── SINGLE PARCEL SEARCH by address or APN (Task 27)
import re as _re

def _looks_like_apn(q: str) -> bool:
    stripped = _re.sub(r"[-\s]", "", q)
    return len(stripped) >= 10 and _re.fullmatch(r"[\d\-]+", q.strip()) is not None


@router.get("/search/parcel")
async def search_parcel(q: str):
    if not q or len(q) < 3:
        return JSONResponse(status_code=400, content={"error": "Query too short"})
    try:
        if _looks_like_apn(q):
            # Search FL DOR by PARCEL_ID
            from app.collectors.parcel_fl import URL as PARCEL_URL
            params = {
                "where": f"PARCEL_ID='{q.strip()}'",
                "outFields": "PARCEL_ID,OWN_NAME,PHY_ADDR1,PHY_CITY,DOR_UC,JV,LND_VAL,NO_BULDNG,SALE_PRC1,SALE_YR1,LND_SQFOOT",
                "returnGeometry": "true",
                "f": "json",
            }
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(PARCEL_URL, params=params)
                data = resp.json()
            features = data.get("features", [])
            if not features:
                return JSONResponse(status_code=404, content={"error": "Parcel not found for APN: " + q})
            attrs = features[0].get("attributes", {})
            addr = f"{attrs.get('PHY_ADDR1','')}, {attrs.get('PHY_CITY','')}, FL"
            parcel = ParcelInput(address=addr, apn=q)
        else:
            parcel = ParcelInput(address=q)

        result = await _process_parcel_safe(parcel)
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
