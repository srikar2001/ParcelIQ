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
from app.collectors.parcel_fl import get_parcel_data
from app.collectors.osm_bundle import get_osm_bundle, BUNDLE_ERROR as _OSM_BUNDLE_ERROR
from app.collectors.roads_tiger import get_road_access
from app.collectors.elevation import get_elevation
from app.collectors.soil import get_soil_data
from app.collectors.evacuation import get_evacuation_zone
from app.collectors.easement import get_conservation_easement
from app.core.insight_engine import score_parcel
from app.core.cache import get_cached_result, save_cached_result

router = APIRouter(prefix="/api")

BATCH_SIZE    = 20        # parcels processed in parallel per chunk
_API_TIMEOUT  = 16.0      # default per external API call (HTTP time only — queue wait excluded)
_PARCEL_TIMEOUT = 240.0   # hard cap per individual parcel (includes per-host queue waits)
WEEKLY_LIMIT  = 1_000_000   # effectively unlimited — free during beta (was 1000)

# ── GLOBAL parcel cap — the guardrail that keeps one user's huge upload from
# taking down the whole app. It bounds how many parcels are actively screened
# at once ACROSS ALL batches and ALL users (module-level = process-wide). Each
# parcel fires ~6 Supabase spatial queries, so this caps total batch DB load to
# roughly 6x this number, leaving the shared Micro Postgres headroom to keep
# serving login / dashboard / admin. A 5k-parcel upload once pegged the DB and
# locked everyone out because there was NO such cap (all parcels fired at once).
# Interactive single-parcel searches deliberately bypass this (one parcel is
# cheap). Raise via BATCH_PARCEL_CONCURRENCY after a Supabase compute bump.
# Default 4 is tuned for the free Nano compute — deliberately conservative so a
# big batch trickles through instead of exhausting the daily Burst Disk I/O and
# taking the whole instance down (as a 5k upload once did). Big batches are
# slower as a result; that's the free-tier trade-off. Raise this env var after a
# compute upgrade to make batches fast again.
_MAX_PARCEL_CONCURRENCY = int(os.environ.get('BATCH_PARCEL_CONCURRENCY', '4'))
_SEM_PARCELS  = asyncio.Semaphore(_MAX_PARCEL_CONCURRENCY)

# ── Per-host concurrency limits. External services rate-limit per IP; a single
# global semaphore let one 20-parcel chunk fire 60+ concurrent Overpass calls
# (limit ~2) so road/waterway/power data failed on nearly every batch row.
# Local Supabase PostGIS RPCs (OVERPASS/FEMA/USFWS/EASE) share the same 1GB
# Micro compute that serves interactive traffic, so they're kept LOW on purpose
# — batches must never consume all the DB's capacity. Tune up only alongside a
# compute bump. External-only collectors (TIGER/FLDOR/USGS/EPA/SOIL/EVAC) don't
# touch Postgres, so they don't threaten interactive uptime.
_SEM_OVERPASS = asyncio.Semaphore(6)   # waterways + powerlines (local Postgres)
_SEM_FEMA     = asyncio.Semaphore(5)   # flood zones (local Postgres)
_SEM_USFWS    = asyncio.Semaphore(4)   # wetlands + critical habitat (local, heaviest)
_SEM_TIGER    = asyncio.Semaphore(8)   # Census TIGERweb — road access (external)
_SEM_FLDOR    = asyncio.Semaphore(8)   # FL DOR statewide cadastral (external)
_SEM_USGS     = asyncio.Semaphore(12)  # elevation (Open-Meteo, external)
_SEM_EPA      = asyncio.Semaphore(5)
_SEM_SOIL     = asyncio.Semaphore(5)   # USDA soil data access (external)
_SEM_EVAC     = asyncio.Semaphore(5)   # FL FDEM evacuation zones (external)
_SEM_EASE     = asyncio.Semaphore(6)   # conservation easements (local Postgres)
_SUPABASE_URL     = os.environ.get('SUPABASE_URL', '')
# Deployments have used several names for these keys — accept any of them
_SUPABASE_KEY     = (os.environ.get('SUPABASE_ANON_KEY')
                     or os.environ.get('SUPABASE_KEY', ''))
_SUPABASE_SVC_KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
                     or os.environ.get('SUPABASE_SERVICE_KEY')
                     or _SUPABASE_KEY)  # bypasses RLS


# Per-email overrides are moot now that everyone is effectively unlimited,
# but kept so a specific user could still be raised further if ever needed.
_VIP_LIMITS: dict[str, int] = {}

class _AuthUnavailable(Exception):
    """Token could not be verified because the verification infrastructure is
    missing/unreachable — distinct from the token actually being rejected."""


async def _user_id_from_token(token: str) -> Optional[tuple[str, str]]:
    """Returns (user_id, email), None if Supabase rejected the token, or raises
    _AuthUnavailable if verification itself couldn't run."""
    if not token:
        return None
    if not _SUPABASE_URL or not (_SUPABASE_KEY or _SUPABASE_SVC_KEY):
        raise _AuthUnavailable("Supabase URL/key env vars not configured")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f'{_SUPABASE_URL}/auth/v1/user',
                headers={'Authorization': f'Bearer {token}',
                         'apikey': _SUPABASE_KEY or _SUPABASE_SVC_KEY},
            )
    except Exception as e:
        raise _AuthUnavailable(str(e))
    if r.status_code != 200:
        return None
    data = r.json()
    uid = data.get('id')
    email = data.get('email', '')
    return (uid, email) if uid else None


def _limit_for(email: str) -> int:
    return _VIP_LIMITS.get(email, WEEKLY_LIMIT)


# ── Admin overrides (plan / daily limit / suspended), stored as reserved keys
# in the existing `reports` KV table because PostgREST can't ALTER TABLE.
# See routers/admin.py for the write side.
import time as _time_mod

_user_override_cache: dict = {"ts": 0.0, "data": {}}


async def _override_get_all(prefix: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"{_SUPABASE_URL}/rest/v1/reports",
                params={"select": "address,report_json", "address": f"like.{prefix}%", "limit": "2000"},
                headers=_SB_HEADERS_SVC(),
            )
        out = {}
        for row in (r.json() if r.status_code == 200 else []):
            raw = row.get("report_json")
            out[row["address"].removeprefix(prefix)] = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return out
    except Exception:
        return {}


async def get_user_override(user_id: str) -> dict:
    now = _time_mod.monotonic()
    if now - _user_override_cache["ts"] > 60:
        _user_override_cache["data"] = await _override_get_all("admin:user:")
        _user_override_cache["ts"] = now
    return _user_override_cache["data"].get(user_id, {})


def _bust_override_cache() -> None:
    _user_override_cache["ts"] = 0.0

async def _weekly_usage(user_id: str) -> int:
    """Usage since midnight UTC today — the limit is 1,000/day."""
    if not user_id or not _SUPABASE_URL:
        return 0
    try:
        now = datetime.now(timezone.utc)
        since = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
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
_ROADS_DEFAULT     = {"road_found": None, "road_surface": "error", "road_type": None, "road_name": None, "road_distance_m": None, "road_private": None, "road_nearest_lat": None, "road_nearest_lng": None, "source": "US Census TIGER"}
_PARCEL_DEFAULT    = {"found": False, "source": "FL DOR Cadastral", "geometry": []}
_WATERWAYS_DEFAULT = {"waterway_nearby": False, "waterway_type": None, "waterway_nearest_lat": None, "waterway_nearest_lng": None, "source": "OpenStreetMap"}
_ELEVATION_DEFAULT = {"elevation_ft": None, "source": "USGS National Elevation Dataset"}
_SOIL_DEFAULT      = {"soil_drainage": None, "soil_name": None, "septic_suitable": None, "source": "USDA Web Soil Survey"}
_EVAC_DEFAULT      = {"evac_zone": None, "evac_risk": None, "source": "FL Division of Emergency Management"}
_POWERLINES_DEFAULT = {"powerline_nearby": None, "powerline_distance": None, "powerline_nearest_lat": None, "powerline_nearest_lng": None, "source": "OpenStreetMap"}
_EASEMENT_DEFAULT  = {"easement_found": False, "easement_type": None, "source": "USDA NRCS"}

_JOBS_TABLE = "batch_jobs"
_SB_HEADERS_SVC = lambda: {
    "apikey": _SUPABASE_SVC_KEY,
    "Authorization": f"Bearer {_SUPABASE_SVC_KEY}",
    "Content-Type": "application/json",
}


async def _job_create(job_id: str, total: int, name: str | None = None) -> None:
    """NOTE: the queue path writes ONLY to batch_jobs (polling state). The
    frontend is the single writer of batches/parcel_results after it confirms
    completion, so a run can never produce duplicate batch records server-side."""
    if not _SUPABASE_URL:
        return
    base = {"id": job_id, "status": "queued", "progress": 0, "total": total,
            "results": [], "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if name:
                # best effort: include the display name if the column exists
                r = await client.post(f"{_SUPABASE_URL}/rest/v1/{_JOBS_TABLE}",
                                      json={**base, "name": name[:200]},
                                      headers={**_SB_HEADERS_SVC(), "Prefer": "return=minimal"})
                if r.status_code in (200, 201, 204):
                    return
            await client.post(f"{_SUPABASE_URL}/rest/v1/{_JOBS_TABLE}", json=base,
                              headers={**_SB_HEADERS_SVC(), "Prefer": "return=minimal"})
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
    name: Optional[str] = None  # display name, echoed back so processing and save agree
    cache_bust: bool = False  # re-screens set this — the whole point is fresh data, not the 24h cache


async def _safe(factory, default, sem, timeout: float = _API_TIMEOUT, retry_if=None, name: str = ""):
    """Run a collector under its host's semaphore with one retry (2s backoff).

    `factory` is a zero-arg callable returning a fresh coroutine (needed for the
    retry). The semaphore is acquired OUTSIDE the timeout so time spent queued
    behind the per-host limit doesn't count against the collector's budget.
    `retry_if(result)` marks soft failures (collectors catch their own
    exceptions and return error-shaped defaults) as retryable.
    """
    result = None
    for attempt in range(2):
        try:
            async with sem:
                result = await asyncio.wait_for(factory(), timeout=timeout)
        except Exception:
            result = None
        failed = result is None or (retry_if is not None and retry_if(result))
        if not failed:
            return result
        if attempt == 0:
            print(f"[Retry] {name or 'collector'} failed — retrying in 2s")
            await asyncio.sleep(2.0)
    return result if result is not None else default


async def _osm_best_effort(lat: float, lng: float) -> dict:
    """Waterways + powerlines are nice-to-have signals (-5 score weight max).
    Overpass is the only rate-limited-to-2 host left; when it's congested a
    parcel must NOT wait minutes for it — cap the queue wait and shed load.

    _collect_all() gathers every collector in parallel and only returns once
    the SLOWEST one finishes, so this cap effectively sets a floor on every
    screen/re-screen's total latency. The previous caps (90s queue wait +
    60s fetch = up to 150s) directly contradicted the "not minutes" comment
    above and were the dominant cause of screens feeling slow — every other
    collector's worst case tops out around 50s. 20s+25s keeps this from
    being the long pole while still giving Overpass a real chance to answer."""
    try:
        await asyncio.wait_for(_SEM_OVERPASS.acquire(), timeout=20.0)
    except asyncio.TimeoutError:
        return dict(_OSM_BUNDLE_ERROR)
    try:
        return await asyncio.wait_for(get_osm_bundle(lat, lng), timeout=25.0)
    except Exception:
        return dict(_OSM_BUNDLE_ERROR)
    finally:
        _SEM_OVERPASS.release()


async def _collect_all(lat: float, lng: float) -> tuple:
    """Fan out all data collectors for one coordinate under per-host limits."""
    (
        flood, wetlands, habitat, epa, parcel_data, roads, osm,
        elevation, soil, evac, easement
    ) = await asyncio.gather(
        _safe(lambda: get_flood_zone(lat, lng), _FLOOD_DEFAULT, _SEM_FEMA,
              timeout=25.0, retry_if=lambda r: r.get("zone") == "ERROR", name="flood"),
        _safe(lambda: get_wetlands(lat, lng), _WETLANDS_DEFAULT, _SEM_USFWS,
              timeout=16.0, retry_if=lambda r: r.get("error") is True, name="wetlands"),
        _safe(lambda: get_critical_habitat(lat, lng), _HABITAT_DEFAULT, _SEM_USFWS,
              timeout=16.0, retry_if=lambda r: r.get("data_available") is False, name="habitat"),
        _safe(lambda: get_epa_superfund(lat, lng), _EPA_DEFAULT, _SEM_EPA,
              timeout=16.0, name="epa"),
        _safe(lambda: get_parcel_data(lat, lng), _PARCEL_DEFAULT, _SEM_FLDOR,
              timeout=16.0, retry_if=lambda r: not r.get("found"), name="parcel"),
        _safe(lambda: get_road_access(lat, lng), _ROADS_DEFAULT, _SEM_TIGER,
              timeout=16.0, retry_if=lambda r: r.get("road_surface") == "error", name="roads"),
        _osm_best_effort(lat, lng),
        _safe(lambda: get_elevation(lat, lng), _ELEVATION_DEFAULT, _SEM_USGS,
              timeout=12.0, retry_if=lambda r: r.get("elevation_ft") is None, name="elevation"),
        _safe(lambda: get_soil_data(lat, lng), _SOIL_DEFAULT, _SEM_SOIL,
              timeout=16.0, retry_if=lambda r: r.get("soil_drainage") is None, name="soil"),
        _safe(lambda: get_evacuation_zone(lat, lng), _EVAC_DEFAULT, _SEM_EVAC,
              timeout=16.0, name="evac"),
        _safe(lambda: get_conservation_easement(lat, lng), _EASEMENT_DEFAULT, _SEM_EASE,
              timeout=16.0, name="easement"),
    )
    waterways  = osm.get("waterways") or _WATERWAYS_DEFAULT
    powerlines = osm.get("powerlines") or _POWERLINES_DEFAULT
    return flood, wetlands, habitat, epa, roads, parcel_data, waterways, elevation, soil, evac, powerlines, easement


async def _process_parcel(parcel: ParcelInput, skip_cache: bool = False) -> dict:
    address = parcel.address

    # Check cache first (Task 28) — re-screens set skip_cache to force fresh data
    if not skip_cache:
        cached = await get_cached_result(address)
        if cached:
            cached["cached"] = True
            return cached

    geo = await geocode(address)
    if geo.get("status") != "ok":
        return _geocode_error_result(address, geo)

    lat, lng = geo["lat"], geo["lng"]
    out = await _screen_coordinate(address, geo, lat, lng)
    # Save to cache (fire and forget) — refreshes the cache even on a re-screen
    asyncio.create_task(save_cached_result(address, out))
    return out


async def _process_parcel_pregeocoded(parcel: ParcelInput, geo: dict, skip_cache: bool = False) -> dict:
    """Like _process_parcel but skips geocoding — uses pre-fetched geo dict."""
    address = parcel.address
    if not skip_cache:
        cached = await get_cached_result(address)
        if cached:
            cached["cached"] = True
            return cached

    lat, lng = geo["lat"], geo["lng"]
    out = await _screen_coordinate(address, geo, lat, lng)
    asyncio.create_task(save_cached_result(address, out))
    return out


async def _screen_coordinate(address: str, geo: dict, lat: float, lng: float) -> dict:
    """Collect all data sources for a coordinate, score it, and build the result."""
    (
        flood, wetlands, habitat, epa, roads, parcel_data, waterways,
        elevation, soil, evac, powerlines, easement
    ) = await _collect_all(lat, lng)

    result = score_parcel(
        flood, wetlands, habitat, epa, roads, parcel_data, waterways,
        elevation=elevation, soil=soil, evac=evac,
        powerlines=powerlines, easement=easement,
    )

    return {
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
            "confidence_pct": result.get("confidence_pct"),
            "sources_ok": result.get("sources_ok"),
            "sources_total": result.get("sources_total"),
            "county": parcel_data.get("county"),
            "parcel_id": parcel_data.get("parcel_id"),
            "acreage": parcel_data.get("acreage"),
            "building_count": parcel_data.get("building_count"),
            "owner": parcel_data.get("owner"),
            "owner_mailing_address": parcel_data.get("owner_mailing_address"),
            "county_address_on_file": parcel_data.get("county_address_on_file"),
            "land_use_code": parcel_data.get("land_use_code"),
            "last_sale_price": parcel_data.get("last_sale_price"),
            "last_sale_year": parcel_data.get("last_sale_year"),
            "prior_sale_price": parcel_data.get("prior_sale_price"),
            "prior_sale_year": parcel_data.get("prior_sale_year"),
            "just_value": parcel_data.get("just_value"),
            # True assessed value (AV_NSD) when the county reports one; JV as fallback
            "assessed_value": parcel_data.get("assessed_value_nsd") or parcel_data.get("just_value"),
            "taxable_value": parcel_data.get("taxable_value_nsd"),
            "land_value": parcel_data.get("land_value"),
            "year_built": parcel_data.get("year_built"),
            "living_area_sqft": parcel_data.get("living_area_sqft"),
            "sale_price_likely_market_value": (
                True  if (parcel_data.get("last_sale_price") or 0) >= 1000
                else False if 0 < (parcel_data.get("last_sale_price") or 0) < 1000
                else None
            ),
            "geometry": parcel_data.get("geometry", []),
            "elevation_ft": elevation.get("elevation_ft"),
            "soil_drainage": soil.get("soil_drainage"),
            "soil_name": soil.get("soil_name"),
            "septic_suitable": soil.get("septic_suitable"),
            "evac_zone": evac.get("evac_zone"),
            "evac_risk": evac.get("evac_risk"),
            "evac_county": evac.get("evac_county"),
            "evac_county_zone": evac.get("evac_county_zone"),
            "flood_zone_subtype": flood.get("zone_subtype"),
            "base_flood_elevation_ft": flood.get("base_flood_elevation_ft"),
            "powerline_nearby": powerlines.get("powerline_nearby"),
            "powerline_distance": powerlines.get("powerline_distance"),
            "powerline_nearest_lat": powerlines.get("powerline_nearest_lat"),
            "powerline_nearest_lng": powerlines.get("powerline_nearest_lng"),
            "easement_found": easement.get("easement_found"),
            "easement_type": easement.get("easement_type"),
            "wetland_type": wetlands.get("wetland_type"),
            "wetland_code": wetlands.get("wetland_code"),
            "habitat_species": habitat.get("species", []),
            "contamination_sites": epa.get("sites", []),
            "road_type": roads.get("road_type"),
            "road_surface": roads.get("road_surface"),
            "road_name": roads.get("road_name"),
            "road_distance_m": roads.get("road_distance_m"),
            "road_private": roads.get("road_private"),
            "road_nearest_lat": roads.get("road_nearest_lat"),
            "road_nearest_lng": roads.get("road_nearest_lng"),
            "waterway_type": waterways.get("waterway_type"),
            "waterway_distance": waterways.get("distance_approx"),
            "waterway_nearest_lat": waterways.get("waterway_nearest_lat"),
            "waterway_nearest_lng": waterways.get("waterway_nearest_lng"),
        },
        "_lat": lat,
        "_lng": lng,
        "sources": result["sources_checked"],
        "error": None,
        "cached": False,
    }


async def _process_parcel_safe(parcel: ParcelInput, skip_cache: bool = False) -> dict:
    try:
        return await asyncio.wait_for(_process_parcel(parcel, skip_cache), timeout=_PARCEL_TIMEOUT)
    except asyncio.TimeoutError:
        return _timeout_result(parcel.address)
    except Exception as e:
        return _error_result(parcel.address, str(e))


async def _process_parcel_pregeocoded_safe(parcel: ParcelInput, geo: dict, timeout: float = _PARCEL_TIMEOUT, skip_cache: bool = False) -> dict:
    # Acquire the global parcel slot OUTSIDE the processing timeout — a parcel
    # queued behind others (waiting for the shared DB budget) must not have that
    # wait counted against its own timeout. This semaphore is what stops a giant
    # batch from firing thousands of concurrent spatial queries at the DB; the
    # rest of the batch simply waits its turn instead of drowning the instance.
    async with _SEM_PARCELS:
        try:
            return await asyncio.wait_for(_process_parcel_pregeocoded(parcel, geo, skip_cache), timeout=timeout)
        except asyncio.TimeoutError:
            return _timeout_result(parcel.address)
        except Exception as e:
            return _error_result(parcel.address, str(e))


def _timeout_result(address: str) -> dict:
    # note: flags stays empty — a system timeout is not a parcel risk flag
    return {
        "address": address, "verdict": "ERROR", "score": None,
        "auto_kill": False, "auto_kill_reason": None,
        "flags": [],
        "positives": [], "parcel_info": {}, "sources": [],
        "error": "Timed out. The data sources were too slow, retry this one.",
    }


def _error_result(address: str, msg: str, suggestions: list | None = None) -> dict:
    return {
        "address": address, "verdict": "ERROR", "score": None,
        "auto_kill": False, "auto_kill_reason": None,
        "flags": [], "positives": [], "parcel_info": {}, "sources": [],
        "error": msg,
        "suggestions": suggestions or [],
    }


def _geocode_error_result(address: str, geo: dict) -> dict:
    """Map a non-ok geocode status to a user-facing ERROR result."""
    status = geo.get("status")
    if status == "wrong_state":
        return _error_result(
            address,
            f"This looks like a {geo.get('state', 'non-Florida')} address "
            f"({geo.get('formatted_address', '')}). ParceliQ covers Florida only.",
        )
    if status == "low_confidence":
        suggestions = geo.get("suggestions") or []
        if suggestions:
            return _error_result(
                address,
                f"Address not found. Did you mean {suggestions[0]}?",
                suggestions=suggestions,
            )
        return _error_result(address, "Address not found.")
    return _error_result(address, "Address not found. Enter a full street address.")


async def _check_batch_auth(authorization: Optional[str], parcel_count: int) -> Optional[JSONResponse]:
    """Require a valid Supabase token and enforce the weekly limit.
    Returns an error response to send, or None if the request may proceed.
    Screening endpoints burn paid geocoding credits — they must not be open.

    Fail-open ONLY when verification infrastructure itself is down/missing:
    blocking every paying customer over a config drift is worse than briefly
    tolerating anonymous calls (which the frontend never makes anyway)."""
    token = (authorization or '').removeprefix('Bearer ').strip()
    try:
        id_email = await _user_id_from_token(token)
    except _AuthUnavailable as e:
        print(f"[Auth] WARNING: token verification unavailable ({e}) — allowing request")
        return None
    if not id_email:
        return JSONResponse(status_code=401, content={'error': 'Sign in to screen parcels.'})
    user_id, email = id_email
    override = await get_user_override(user_id)
    if override.get("suspended"):
        return JSONResponse(status_code=403, content={'error': 'This account is suspended. Contact support.'})
    limit = override.get("daily_limit") if override.get("daily_limit") is not None else _limit_for(email)
    used  = await _weekly_usage(user_id)
    if used + parcel_count > limit:
        return JSONResponse(
            status_code=429,
            content={
                'limit_exceeded': True,
                'used': used,
                'limit': limit,
                'message': 'Daily screening limit reached',
            },
        )
    return None


@router.get("/usage")
async def get_usage(authorization: Optional[str] = Header(None)):
    """Current user's daily screening usage and effective limit — the
    admin-set per-user override if one exists, otherwise the plan default.
    The frontend usage bar and the pre-submit client-side check both read
    this instead of a hardcoded number, so an admin override actually shows
    up for the user instead of silently applying only server-side."""
    token = (authorization or '').removeprefix('Bearer ').strip()
    try:
        id_email = await _user_id_from_token(token)
    except _AuthUnavailable:
        return JSONResponse(status_code=200, content={"used": 0, "limit": WEEKLY_LIMIT})
    if not id_email:
        return JSONResponse(status_code=401, content={"error": "Sign in required."})
    user_id, email = id_email
    override = await get_user_override(user_id)
    limit = override.get("daily_limit") if override.get("daily_limit") is not None else _limit_for(email)
    used = await _weekly_usage(user_id)
    return JSONResponse(content={"used": used, "limit": limit})


async def _split_cached(parcels: list, skip_cache: bool) -> tuple[list, list]:
    """Split parcels into (already-cached result pairs, parcels still needing a
    geocode). A cache hit skips BOTH geocoding (a paid Geocodio credit) and
    screening. skip_cache (a re-screen) forces everything to be re-fetched.

    Cache reads run concurrently so the pre-pass adds ~one round-trip, not one
    per parcel."""
    if skip_cache:
        return [], list(parcels)
    hits = await asyncio.gather(*[get_cached_result(p.address) for p in parcels])
    cached_pairs, to_geocode = [], []
    for p, c in zip(parcels, hits):
        if isinstance(c, dict) and c:
            c = dict(c)
            c["cached"] = True
            cached_pairs.append((p, c))
        else:
            to_geocode.append(p)
    return cached_pairs, to_geocode


@router.post("/batch/stream")
async def batch_screen_stream(
    request: BatchRequest,
    authorization: Optional[str] = Header(None),
):
    denied = await _check_batch_auth(authorization, len(request.parcels))
    if denied is not None:
        return denied

    async def event_generator():
        parcels = request.parcels
        total   = len(parcels)
        all_results: list = []
        completed = 0

        try:
            yield f'data: {json.dumps({"type":"start","total":total})}\n\n'

            # ── Step 0: cache-first — a cached address skips geocoding entirely
            # (no Geocodio credit) and its result streams out immediately.
            cached_pairs, to_geocode = await _split_cached(parcels, request.cache_bust)
            for p, r in cached_pairs:
                all_results.append(r)
                completed += 1
                pct = int((completed / total) * 100)
                yield f'data: {json.dumps({"type":"progress","completed":completed,"total":total,"percent":pct,"latest":r})}\n\n'

            # ── Step 1: batch-geocode ONLY the uncached addresses ────────────────
            addresses = [p.address for p in to_geocode]
            geo_map   = await geocode_batch(addresses) if addresses else {}

            # Immediately emit ERROR for any that failed geocoding
            geocoded_parcels = []
            for p in to_geocode:
                geo = geo_map.get(p.address) or {"status": "not_found"}
                if geo.get("status") == "ok":
                    geocoded_parcels.append((p, geo))
                else:
                    r = _geocode_error_result(p.address, geo)
                    all_results.append(r)
                    completed += 1
                    pct = int((completed / total) * 100)
                    yield f'data: {json.dumps({"type":"progress","completed":completed,"total":total,"percent":pct,"latest":r})}\n\n'

            # ── Step 2: process in parallel chunks (cache already checked above,
            # so skip_cache=True avoids a redundant cache read per parcel) ────────
            for i in range(0, len(geocoded_parcels), BATCH_SIZE):
                chunk = geocoded_parcels[i:i + BATCH_SIZE]
                chunk_results = await asyncio.gather(
                    *[_process_parcel_pregeocoded_safe(p, geo, skip_cache=True) for p, geo in chunk]
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
                    r = _error_result(p.address, "Internal error. Retry this parcel")
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
    denied = await _check_batch_auth(authorization, len(request.parcels))
    if denied is not None:
        return denied

    all_results = []
    parcels = request.parcels

    for i in range(0, len(parcels), BATCH_SIZE):
        chunk = parcels[i : i + BATCH_SIZE]
        chunk_results = await asyncio.gather(
            *[_process_parcel_safe(p, request.cache_bust) for p in chunk]
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


# ── QUEUE for large batches. The frontend polls /batch/status every ~2.5s, so
# results must be written progressively — per parcel, throttled — not only at
# the end. (The old per-chunk/no-results updates meant a 100-parcel batch
# showed nothing for its entire runtime.)
async def _process_batch_background(job_id: str, parcels: list, skip_cache: bool = False) -> None:
    import time as _time
    accumulated: list = []
    last_flush = 0.0

    async def _flush(force: bool = False) -> None:
        nonlocal last_flush
        now = _time.monotonic()
        if not force and now - last_flush < 2.0:
            return
        last_flush = now
        await _job_update(job_id, "processing", len(accumulated), accumulated)

    await _job_update(job_id, "processing", 0, [])
    try:
        # Cache-first: cached addresses skip geocoding (no Geocodio credit) and
        # go straight into the results.
        cached_pairs, to_geocode = await _split_cached(parcels, skip_cache)
        for _p, r in cached_pairs:
            accumulated.append(r)

        # Batch-geocode ONLY the uncached addresses
        addresses = [p.address for p in to_geocode]
        geo_map   = await geocode_batch(addresses) if addresses else {}

        # Parcels that failed geocoding get immediate ERROR results
        geocoded_parcels = []
        for p in to_geocode:
            geo = geo_map.get(p.address) or {"status": "not_found"}
            if geo.get("status") == "ok":
                geocoded_parcels.append((p, geo))
            else:
                accumulated.append(_geocode_error_result(p.address, geo))
        if accumulated:
            await _flush(force=True)

        # Launch every parcel at once — the per-host semaphores are the real
        # concurrency governors, and sequential chunks left the rate-limited
        # hosts idle at each chunk boundary (convoy effect). The generous
        # timeout covers time spent queued behind the per-host limits.
        # Cache already checked above, so skip_cache=True here.
        queue_timeout = max(_PARCEL_TIMEOUT, 30.0 * len(geocoded_parcels))
        tasks = [asyncio.create_task(_process_parcel_pregeocoded_safe(p, geo, timeout=queue_timeout, skip_cache=True))
                 for p, geo in geocoded_parcels]
        for fut in asyncio.as_completed(tasks):
            accumulated.append(await fut)
            await _flush()

        await _job_update(job_id, "complete", len(accumulated), accumulated)
    except Exception as e:
        print(f"[Jobs] Background error: {e}")
        await _job_update(job_id, "error", len(accumulated), accumulated)


@router.post("/batch/queue")
async def batch_queue(
    request: BatchRequest,
    authorization: Optional[str] = Header(None),
):
    denied = await _check_batch_auth(authorization, len(request.parcels))
    if denied is not None:
        return denied
    job_id = str(uuid4())
    await _job_create(job_id, len(request.parcels), request.name)
    asyncio.create_task(_process_batch_background(job_id, request.parcels, request.cache_bust))
    return {"job_id": job_id, "total": len(request.parcels)}


@router.get("/batch/status/{job_id}")
async def batch_status(job_id: str, since: int = 0):
    """Poll a background batch. Returns partial results as they complete;
    pass ?since=N (count already received) to get only the new ones."""
    job = await _job_read(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    results = job.get("results") or []
    since = max(0, int(since))
    return {
        "status": job["status"],
        "progress": job["progress"],
        "total": job["total"],
        "results": results[since:],
        "offset": since,
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


# ── Address type-ahead — Esri World Geocoder via app.collectors.suggest.
# (The old FL DOR cadastral LIKE queries timed out upstream on every request,
# so autocomplete was silently dead in production.)
from app.collectors.suggest import suggest_addresses as _suggest_addresses


@router.get("/autocomplete")
async def address_autocomplete(q: str = ""):
    try:
        return {"suggestions": await _suggest_addresses(q)}
    except Exception:
        return {"suggestions": []}


@router.get("/geocodio-suggest")
async def geocodio_suggest(q: str = ""):
    try:
        return {"suggestions": [{"address": a} for a in await _suggest_addresses(q)]}
    except Exception:
        return {"suggestions": []}



@router.get("/cache/test")
async def test_cache():
    from app.core.cache import get_cached_result, save_cached_result
    test_addr = "parceliq-cache-test-001"
    await save_cached_result(test_addr, {"test": True, "verdict": "PURSUE", "score": 85})
    result = await get_cached_result(test_addr)
    return {"cache_working": result is not None, "result": result}
