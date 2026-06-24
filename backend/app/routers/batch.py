import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.collectors.geocodio import geocode
from app.collectors.flood import get_flood_zone
from app.collectors.wetlands import get_wetlands
from app.collectors.critical_habitat import get_critical_habitat
from app.collectors.epa import get_epa_superfund
from app.collectors.roads_osm import get_road_type
from app.collectors.parcel_fl import get_parcel_data
from app.core.insight_engine import score_parcel

router = APIRouter(prefix="/api")

BATCH_SIZE = 10

_FLOOD_DEFAULT    = {"zone": "UNKNOWN", "sfha": False, "source": "FEMA NFHL"}
_WETLANDS_DEFAULT = {"wetland_on_parcel": False, "wetland_nearby": False, "wetland_type": None, "wetland_code": None, "source": "USFWS NWI"}
_HABITAT_DEFAULT  = {"habitat_found": False, "species": [], "source": "USFWS Critical Habitat"}
_EPA_DEFAULT      = {"contamination_found": False, "sites": [], "source": "EPA ECHO Superfund"}
_ROADS_DEFAULT    = {"road_found": False, "road_surface": "none", "road_type": None, "source": "OpenStreetMap"}
_PARCEL_DEFAULT   = {"found": False, "source": "FL DOR Cadastral"}


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
        }

    lat, lng = geo["lat"], geo["lng"]

    flood, wetlands, habitat, epa, roads, parcel_data = await asyncio.gather(
        _safe(get_flood_zone(lat, lng), _FLOOD_DEFAULT),
        _safe(get_wetlands(lat, lng), _WETLANDS_DEFAULT),
        _safe(get_critical_habitat(lat, lng), _HABITAT_DEFAULT),
        _safe(get_epa_superfund(lat, lng), _EPA_DEFAULT),
        _safe(get_road_type(lat, lng), _ROADS_DEFAULT),
        _safe(get_parcel_data(lat, lng), _PARCEL_DEFAULT),
    )

    result = score_parcel(flood, wetlands, habitat, epa, roads, parcel_data)

    return {
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
        },
        "sources": result["sources_checked"],
        "error": None,
    }


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


@router.post("/batch")
async def batch_screen(request: BatchRequest):
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
