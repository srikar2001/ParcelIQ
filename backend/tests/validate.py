import asyncio
import os
import sys

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collectors.geocodio import geocode
from app.collectors.flood import get_flood_zone
from app.collectors.wetlands import get_wetlands
from app.collectors.critical_habitat import get_critical_habitat
from app.collectors.epa import get_epa_superfund
from app.collectors.roads_osm import get_road_type
from app.collectors.parcel_fl import get_parcel_data
from app.core.insight_engine import score_parcel

PARCELS = [
    ("1801 Olde Middle Gulf Dr, Sanibel FL 33957",           ["KILL"]),
    ("5750 W County Hwy 30A, Santa Rosa Beach FL 32459",     ["REVIEW"]),
    ("SE Parliament Trail, Lee FL 32052",                    ["PURSUE"]),
    ("St Johns Bluff Rd S, Jacksonville FL 32246",           ["REVIEW"]),
    ("16970 NW 312th St, Okeechobee FL 34972",               ["REVIEW", "PURSUE"]),
]

_FLOOD_D    = {"zone": "UNKNOWN", "sfha": False, "source": "FEMA NFHL"}
_WET_D      = {"wetland_on_parcel": False, "wetland_nearby": False, "wetland_type": None, "wetland_code": None, "source": "USFWS NWI"}
_HAB_D      = {"habitat_found": False, "species": [], "source": "USFWS Critical Habitat"}
_EPA_D      = {"contamination_found": False, "sites": [], "source": "EPA ECHO Superfund"}
_ROAD_D     = {"road_found": False, "road_surface": "none", "road_type": None, "source": "OpenStreetMap"}
_PARCEL_D   = {"found": False, "source": "FL DOR Cadastral"}


async def _safe(coro, default):
    try:
        return await coro
    except Exception:
        return default


async def run_parcel(address: str):
    geo = await geocode(address)
    if geo is None:
        return None, None, ["geocode failed"], []

    lat, lng = geo["lat"], geo["lng"]

    flood, wetlands, habitat, epa, roads, parcel = await asyncio.gather(
        _safe(get_flood_zone(lat, lng), _FLOOD_D),
        _safe(get_wetlands(lat, lng), _WET_D),
        _safe(get_critical_habitat(lat, lng), _HAB_D),
        _safe(get_epa_superfund(lat, lng), _EPA_D),
        _safe(get_road_type(lat, lng), _ROAD_D),
        _safe(get_parcel_data(lat, lng), _PARCEL_D),
    )

    result = score_parcel(flood, wetlands, habitat, epa, roads, parcel)
    return result["verdict"], result["score"], result["flags"], result.get("auto_kill_reason")


async def main():
    passed = 0
    total = len(PARCELS)

    print(f"{'ADDRESS':<42} | {'VERDICT':<6} | {'SCORE':<7} | {'FLAGS':<30} | RESULT")
    print("-" * 105)

    for address, expected in PARCELS:
        verdict, score, flags, kill_reason = await run_parcel(address)

        short_addr = address[:40] + ".." if len(address) > 40 else address
        score_str  = "N/A" if score == 0 and verdict == "KILL" else str(score)
        flags_str  = ", ".join(flags)[:30] if flags else "no flags"

        if verdict is None:
            print(f"{short_addr:<42} | {'ERROR':<6} | {'N/A':<7} | {'geocode failed':<30} | FAIL")
            continue

        ok = verdict in expected
        if ok:
            passed += 1

        status = "PASS" if ok else f"FAIL (expected {'/'.join(expected)})"
        print(f"{short_addr:<42} | {verdict:<6} | {score_str:<7} | {flags_str:<30} | {status}")

    print("-" * 105)
    print(f"Summary: {passed}/{total} passed")


if __name__ == "__main__":
    asyncio.run(main())
