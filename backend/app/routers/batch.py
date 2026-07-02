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

from app.collectors.geocodio import geocode, geocode_batch
from app.collectors.flood import get_flood_zone
from app.collectors.wetlands import get_wetlands
from app.collectors.critical_habitat import get_critical_habitat
from app.collectors.epa import get_epa_superfund
from app.collectors.roads_osm import get_road_type
from app.collectors.parcel_fl import get_parcel_data
from app.collectors.waterways import get_waterway_proximity
from app.collectors.elevation import get_elevation
from app.collectors.soil import get_soil_data
from app.collectors.evacuation import get_evacuation_zone
from app.collectors.powerlines import get_powerline_proximity
from app.collectors.easement import get_conservation_easement
from app.core.insight_engine import score_parcel
from app.core.cache import get_cached_result, save_cached_result

router = APIRouter(prefix="/api")

BATCH_SIZE    = 20        # parcels processed in parallel per chunk
_API_TIMEOUT  = 7.0       # per external API call — fast-fail slow sources
_PARCEL_TIMEOUT = 15.0    # hard cap per individual parcel
_API_SEM      = asyncio.Semaphore(120)  # max concurrent external HTTP calls
WEEKLY_LIMIT  = 1000
_SUPABASE_URL     = os.environ.get('SUPABASE_URL', '')
_SUPABASE_KEY     = os.environ.get('SUPABASE_ANON_KEY', '')
_SUPABASE_SVC_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', _SUPABASE_KEY)  # bypasses RLS


_VIP_LIMITS: dict[str, int] = {
    'srikarvudaru1@gmail.com': 10_000,
}

async def _user_id_from_token(token: str) -> Optional[tuple[str, str]]:
    """Returns (user_id, email) or None."""
    if not token or not _SUPABASE_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f'{_SUPABASE_URL}/auth/v1/user',
                headers={'Authorization': f'Bearer {token}', 'apikey': _SUPABASE_KEY},
            )
        if r.status_code != 200:
            return None
        data = r.json()
        uid = data.get('id')
        email = data.get('email', '')
        return (uid, email) if uid else None
    except Exception:
        return None


def _limit_for(email: str) -> int:
    return _VIP_LIMITS.get(email, WEEKLY_LIMIT)

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
                    'apikey': _SUPABASE_SVC_KEY,
                    'Authorization': f'Bearer {_SUPABASE_SVC_KEY}',
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
_ELEVATION_DEFAULT = {"elevation_ft": None, "source": "USGS National Elevation Dataset"}
_SOIL_DEFAULT      = {"soil_drainage": None, "soil_name": None, "septic_suitable": None, "source": "USDA Web Soil Survey"}
_EVAC_DEFAULT      = {"evac_zone": None, "evac_risk": None, "source": "FL Division of Emergency Management"}
_POWERLINES_DEFAULT = {"powerline_nearby": None, "powerline_distance": None, "source": "OpenStreetMap"}
_EASEMENT_DEFAULT  = {"easement_found": False, "easement_type": None, "source": "USDA NRCS"}

_JOBS_TABLE = "batch_jobs"
_SB_HEADERS_SVC = lambda: {
    "apikey": _SUPABASE_SVC_KEY,
    "Authorization": f"Bearer {_SUPABASE_SVC_KEY}",
    "Content-Type": "application/json",
}


async def _job_create(job_id: str, total: int) -> None:
    if not _SUPABASE_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{_SUPABASE_URL}/rest/v1/{_JOBS_TABLE}",
                json={"id": job_id, "status": "queued", "progress": 0, "total": total,
                      "results": [], "created_at": datetime.now(timezone.utc).isoformat()},
                headers={**_SB_HEADERS_SVC(), "Prefer": "return=minimal"},
            )
    except Exception as e:
        print(f"[Jobs] Create error: {e}")


async def _job_update(job_id: str, status: str, progress: int, results: list | None = None) -> None:
    """Update job progress. Only include full results payload on the final 'complete' call."""
    if not _SUPABASE_URL:
        return
    try:
        payload: dict = {"status": status, "progress": progress}
        if results is not None:  # only write results array on final update
            payload["results"] = results
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.patch(
                f"{_SUPABASE_URL}/rest/v1/{_JOBS_TABLE}",
                params={"id": f"eq.{job_id}"},
                json=payload,
                headers={**_SB_HEADERS_SVC(), "Prefer": "return=minimal"},
            )
    except Exception as e:
        print(f"[Jobs] Update error: {e}")


async def _job_read(job_id: str) -> dict | None:
    if not _SUPABASE_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{_SUPABASE_URL}/rest/v1/{_JOBS_TABLE}",
                params={"id": f"eq.{job_id}", "select": "*", "limit": "1"},
                headers=_SB_HEADERS_SVC(),
            )
        rows = r.json()
        return rows[0] if rows else None
    except Exception as e:
        print(f"[Jobs] Read error: {e}")
        return None


class ParcelInput(BaseModel):
    address: str
    apn: Optional[str] = None


class BatchRequest(BaseModel):
    parcels: list[ParcelInput]
    state: str = "FL"


async def _safe(coro, default, timeout: float = _API_TIMEOUT):
    async with _API_SEM:
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
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

    formatted = geo.get("formatted_address", "")
    if formatted and ", FL" not in formatted and "Florida" not in formatted:
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
            "error": "Address not found in Florida — verify and retry",
            "cached": False,
        }

    lat, lng = geo["lat"], geo["lng"]

    (
        flood, wetlands, habitat, epa, roads, parcel_data, waterways,
        elevation, soil, evac, powerlines, easement
    ) = await asyncio.gather(
        _safe(get_flood_zone(lat, lng), _FLOOD_DEFAULT),
        _safe(get_wetlands(lat, lng), _WETLANDS_DEFAULT),
        _safe(get_critical_habitat(lat, lng), _HABITAT_DEFAULT),
        _safe(get_epa_superfund(lat, lng), _EPA_DEFAULT),
        _safe(get_road_type(lat, lng), _ROADS_DEFAULT),
        _safe(get_parcel_data(lat, lng), _PARCEL_DEFAULT),
        _safe(get_waterway_proximity(lat, lng), _WATERWAYS_DEFAULT),
        _safe(get_elevation(lat, lng), _ELEVATION_DEFAULT),
        _safe(get_soil_data(lat, lng), _SOIL_DEFAULT),
        _safe(get_evacuation_zone(lat, lng), _EVAC_DEFAULT),
        _safe(get_powerline_proximity(lat, lng), _POWERLINES_DEFAULT),
        _safe(get_conservation_easement(lat, lng), _EASEMENT_DEFAULT),
    )

    result = score_parcel(
        flood, wetlands, habitat, epa, roads, parcel_data, waterways,
        elevation=elevation, soil=soil, evac=evac,
        powerlines=powerlines, easement=easement,
    )

    out = {
        "address": geo.get("formatted_address", address),
        "verdict": result["verdict"],
        "score": result["score"],
        "auto_kill": result["auto_kill"],
        "auto_kill_reason": result["auto_kill_reason"],
        "flags": result["flags"],
        "kill_flags": result.get("kill_flags", []),
        "review_flags": result.get("review_flags", []),
        "info_flags": result.get("info_flags", []),
        "positives": result["positives"],
        "parcel_info": {
            "acreage": parcel_data.get("acreage"),
            "owner": parcel_data.get("owner"),
            "land_use_code": parcel_data.get("land_use_code"),
            "last_sale_price": parcel_data.get("last_sale_price"),
            "just_value": parcel_data.get("just_value"),
            "land_value": parcel_data.get("land_value"),
            "geometry": parcel_data.get("geometry", []),
            "elevation_ft": elevation.get("elevation_ft"),
            "soil_drainage": soil.get("soil_drainage"),
            "soil_name": soil.get("soil_name"),
            "septic_suitable": soil.get("septic_suitable"),
            "evac_zone": evac.get("evac_zone"),
            "evac_risk": evac.get("evac_risk"),
            "evac_county": evac.get("evac_county"),
            "powerline_nearby": powerlines.get("powerline_nearby"),
            "powerline_distance": powerlines.get("powerline_distance"),
            "easement_found": easement.get("easement_found"),
            "easement_type": easement.get("easement_type"),
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


async def _process_parcel_pregeocoded(parcel: ParcelInput, geo: dict) -> dict:
    """Like _process_parcel but skips geocoding — uses pre-fetched geo dict."""
    address = parcel.address
    cached = await get_cached_result(address)
    if cached:
        cached["cached"] = True
        return cached

    formatted = geo.get("formatted_address", "")
    if formatted and ", FL" not in formatted and "Florida" not in formatted:
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
            "error": "Address not found in Florida — verify and retry",
            "cached": False,
        }

    lat, lng = geo["lat"], geo["lng"]
    (
        flood, wetlands, habitat, epa, roads, parcel_data, waterways,
        elevation, soil, evac, powerlines, easement
    ) = await asyncio.gather(
        _safe(get_flood_zone(lat, lng),             _FLOOD_DEFAULT),
        _safe(get_wetlands(lat, lng),               _WETLANDS_DEFAULT),
        _safe(get_critical_habitat(lat, lng),       _HABITAT_DEFAULT),
        _safe(get_epa_superfund(lat, lng),          _EPA_DEFAULT),
        _safe(get_road_type(lat, lng),              _ROADS_DEFAULT),
        _safe(get_parcel_data(lat, lng),            _PARCEL_DEFAULT),
        _safe(get_waterway_proximity(lat, lng),     _WATERWAYS_DEFAULT),
        _safe(get_elevation(lat, lng),              _ELEVATION_DEFAULT),
        _safe(get_soil_data(lat, lng),              _SOIL_DEFAULT),
        _safe(get_evacuation_zone(lat, lng),        _EVAC_DEFAULT),
        _safe(get_powerline_proximity(lat, lng),    _POWERLINES_DEFAULT),
        _safe(get_conservation_easement(lat, lng),  _EASEMENT_DEFAULT),
    )
    result = score_parcel(
        flood, wetlands, habitat, epa, roads, parcel_data, waterways,
        elevation=elevation, soil=soil, evac=evac,
        powerlines=powerlines, easement=easement,
    )
    out = {
        "address": geo.get("formatted_address", address),
        "verdict": result["verdict"],
        "score": result["score"],
        "auto_kill": result["auto_kill"],
        "auto_kill_reason": result["auto_kill_reason"],
        "flags": result["flags"],
        "kill_flags": result.get("kill_flags", []),
        "review_flags": result.get("review_flags", []),
        "info_flags": result.get("info_flags", []),
        "positives": result["positives"],
        "parcel_info": {
            "acreage": parcel_data.get("acreage"),
            "owner": parcel_data.get("owner"),
            "land_use_code": parcel_data.get("land_use_code"),
            "last_sale_price": parcel_data.get("last_sale_price"),
            "just_value": parcel_data.get("just_value"),
            "land_value": parcel_data.get("land_value"),
            "geometry": parcel_data.get("geometry", []),
            "elevation_ft": elevation.get("elevation_ft"),
            "soil_drainage": soil.get("soil_drainage"),
            "soil_name": soil.get("soil_name"),
            "septic_suitable": soil.get("septic_suitable"),
            "evac_zone": evac.get("evac_zone"),
            "evac_risk": evac.get("evac_risk"),
            "evac_county": evac.get("evac_county"),
            "powerline_nearby": powerlines.get("powerline_nearby"),
            "powerline_distance": powerlines.get("powerline_distance"),
            "easement_found": easement.get("easement_found"),
            "easement_type": easement.get("easement_type"),
        },
        "_lat": lat,
        "_lng": lng,
        "sources": result["sources_checked"],
        "error": None,
        "cached": False,
    }
    asyncio.create_task(save_cached_result(address, out))
    return out


async def _process_parcel_safe(parcel: ParcelInput) -> dict:
    try:
        return await asyncio.wait_for(_process_parcel(parcel), timeout=_PARCEL_TIMEOUT)
    except asyncio.TimeoutError:
        return _timeout_result(parcel.address)
    except Exception as e:
        return _error_result(parcel.address, str(e))


async def _process_parcel_pregeocoded_safe(parcel: ParcelInput, geo: dict) -> dict:
    try:
        return await asyncio.wait_for(_process_parcel_pregeocoded(parcel, geo), timeout=_PARCEL_TIMEOUT)
    except asyncio.TimeoutError:
        return _timeout_result(parcel.address)
    except Exception as e:
        return _error_result(parcel.address, str(e))


def _timeout_result(address: str) -> dict:
    return {
        "address": address, "verdict": "ERROR", "score": None,
        "auto_kill": False, "auto_kill_reason": None,
        "flags": ["Timed out — retry this parcel"],
        "positives": [], "parcel_info": {}, "sources": [],
        "error": "Timed out — data sources were too slow. Retry.",
    }


def _error_result(address: str, msg: str) -> dict:
    return {
        "address": address, "verdict": "ERROR", "score": None,
        "auto_kill": False, "auto_kill_reason": None,
        "flags": [], "positives": [], "parcel_info": {}, "sources": [],
        "error": msg,
    }


@router.post("/batch/stream")
async def batch_screen_stream(
    request: BatchRequest,
    authorization: Optional[str] = Header(None),
):
    if authorization:
        token    = authorization.removeprefix('Bearer ').strip()
        id_email = await _user_id_from_token(token)
        if id_email:
            user_id, email = id_email
            limit = _limit_for(email)
            used  = await _weekly_usage(user_id)
            if used + len(request.parcels) > limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        'limit_exceeded': True,
                        'used': used,
                        'limit': limit,
                        'message': 'Weekly screening limit reached',
                    },
                )

    async def event_generator():
        parcels = request.parcels
        total   = len(parcels)
        all_results: list = []
        completed = 0

        try:
            yield f'data: {json.dumps({"type":"start","total":total})}\n\n'

            # ── Step 1: batch-geocode all addresses in one API call ──────────────
            addresses = [p.address for p in parcels]
            geo_map   = await geocode_batch(addresses)  # {address -> {lat,lng,formatted}}

            # Immediately emit ERROR for any that failed geocoding
            geocoded_parcels = []
            for p in parcels:
                if p.address in geo_map:
                    geocoded_parcels.append((p, geo_map[p.address]))
                else:
                    r = _error_result(p.address, "Geocoding failed — address not found in Florida")
                    all_results.append(r)
                    completed += 1
                    pct = int((completed / total) * 100)
                    yield f'data: {json.dumps({"type":"progress","completed":completed,"total":total,"percent":pct,"latest":r})}\n\n'

            # ── Step 2: process in parallel chunks (no geocoding overhead) ────────
            for i in range(0, len(geocoded_parcels), BATCH_SIZE):
                chunk = geocoded_parcels[i:i + BATCH_SIZE]
                chunk_results = await asyncio.gather(
                    *[_process_parcel_pregeocoded_safe(p, geo) for p, geo in chunk]
                )
                for result in chunk_results:
                    all_results.append(result)
                    completed += 1
                    pct = int((completed / total) * 100)
                    yield f'data: {json.dumps({"type":"progress","completed":completed,"total":total,"percent":pct,"latest":result})}\n\n'

        except Exception as e:
            print(f"[Stream] Unhandled error after {completed}/{total}: {e}")
            # Emit remaining parcels as errors so frontend gets a complete event
            processed_addrs = {r["address"] for r in all_results}
            for p in parcels:
                if p.address not in processed_addrs:
                    r = _error_result(p.address, "Internal error — retry this parcel")
                    all_results.append(r)

        summary = {
            "total":  len(all_results),
            "kill":   sum(1 for r in all_results if r["verdict"] == "KILL"),
            "review": sum(1 for r in all_results if r["verdict"] == "REVIEW"),
            "pursue": sum(1 for r in all_results if r["verdict"] == "PURSUE"),
            "error":  sum(1 for r in all_results if r["verdict"] == "ERROR"),
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
        token    = authorization.removeprefix('Bearer ').strip()
        id_email = await _user_id_from_token(token)
        if id_email:
            user_id, email = id_email
            limit = _limit_for(email)
            used  = await _weekly_usage(user_id)
            if used + len(request.parcels) > limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        'limit_exceeded': True,
                        'used': used,
                        'limit': limit,
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
    accumulated: list = []
    await _job_update(job_id, "processing", 0)
    try:
        # Batch-geocode all addresses in one API call (same as SSE path)
        addresses = [p.address for p in parcels]
        geo_map   = await geocode_batch(addresses)

        # Parcels that failed geocoding get immediate ERROR results
        geocoded_parcels = []
        for p in parcels:
            if p.address in geo_map:
                geocoded_parcels.append((p, geo_map[p.address]))
            else:
                accumulated.append(_error_result(p.address, "Geocoding failed — address not found in Florida"))

        # Process geocoded parcels in parallel chunks
        for i in range(0, len(geocoded_parcels), BATCH_SIZE):
            chunk = geocoded_parcels[i:i + BATCH_SIZE]
            results = await asyncio.gather(*[_process_parcel_pregeocoded_safe(p, geo) for p, geo in chunk])
            accumulated.extend(results)
            await _job_update(job_id, "processing", len(accumulated))

        await _job_update(job_id, "complete", len(accumulated), accumulated)
    except Exception as e:
        print(f"[Jobs] Background error: {e}")
        await _job_update(job_id, "error", len(accumulated), accumulated)


@router.post("/batch/queue")
async def batch_queue(request: BatchRequest):
    job_id = str(uuid4())
    await _job_create(job_id, len(request.parcels))
    asyncio.create_task(_process_batch_background(job_id, request.parcels))
    return {"job_id": job_id, "total": len(request.parcels)}


@router.get("/batch/status/{job_id}")
async def batch_status(job_id: str):
    job = await _job_read(job_id)
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


_ARCGIS_CADASTRAL_URL = "https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/Florida_Statewide_Cadastral/FeatureServer/0/query"

@router.get("/autocomplete")
async def address_autocomplete(q: str = ""):
    if len(q.strip()) < 4:
        return {"suggestions": []}
    try:
        sanitized = _re.sub(r"[^a-zA-Z0-9 ,.\-/]", "", q.strip())
        if not sanitized:
            return {"suggestions": []}
        like_val = sanitized.replace("%", r"\%").replace("_", r"\_")
        where = f"UPPER(PHY_ADDR1) LIKE UPPER('%{like_val}%')"
        params = {
            "where": where,
            "outFields": "PHY_ADDR1,PHY_CITY",
            "returnGeometry": "false",
            "resultRecordCount": 8,
            "f": "json",
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_ARCGIS_CADASTRAL_URL, params=params)
            data = resp.json()
        seen: set[str] = set()
        suggestions: list[str] = []
        for feat in data.get("features", []):
            attrs = feat.get("attributes", {})
            addr1 = (attrs.get("PHY_ADDR1") or "").strip()
            city  = (attrs.get("PHY_CITY") or "").strip()
            if not addr1:
                continue
            full = f"{addr1}, {city}, FL" if city else f"{addr1}, FL"
            if full not in seen:
                seen.add(full)
                suggestions.append(full)
                if len(suggestions) == 8:
                    break
        return {"suggestions": suggestions}
    except Exception:
        return {"suggestions": []}


@router.get("/geocodio-suggest")
async def suggest_address(q: str = ""):
    if len(q) < 4:
        return {"suggestions": []}
    try:
        api_key = os.environ.get("GEOCODIO_API_KEY", "")
        if not api_key:
            return {"suggestions": []}
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                "https://api.geocod.io/v1.7/suggest",
                params={
                    "q": q,
                    "api_key": api_key,
                    "country": "US",
                    "limit": 8,
                }
            )
            data = r.json()
        suggestions = []
        for item in data.get("results", []):
            formatted = item.get("formatted_address", "")
            if not formatted:
                continue
            if ", FL" not in formatted and "Florida" not in formatted:
                continue
            suggestions.append({"address": formatted})
        return {"suggestions": suggestions}
    except Exception:
        return {"suggestions": []}


@router.get("/cache/test")
async def test_cache():
    from app.core.cache import get_cached_result, save_cached_result
    test_addr = "parceliq-cache-test-001"
    await save_cached_result(test_addr, {"test": True, "verdict": "PURSUE", "score": 85})
    result = await get_cached_result(test_addr)
    return {"cache_working": result is not None, "result": result}
