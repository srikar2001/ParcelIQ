"""
Offline check: FL DOR use codes arrive zero-padded ('088'), and government-owned
parcels (086-089) must auto-KILL; 099 ("acreage not ag classified") is private
land and must not.

  backend/venv/bin/python tests/test_scoring_public_land.py
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app.core.insight_engine import score_parcel  # noqa: E402

FLOOD = {"zone": "X", "sfha": False}
WET = {"wetland_on_parcel": False, "wetland_nearby": False}
HAB = {"habitat_found": False, "data_available": True}
EPA = {"contamination_found": False, "sites": []}
ROADS = {"road_found": True, "road_surface": "paved", "road_private": False}


def run(code):
    parcel = {"found": True, "acreage": 1.0, "building_count": 0, "land_use_code": code}
    return score_parcel(FLOOD, WET, HAB, EPA, ROADS, parcel, {})


for code in ("086", "087", "088", "089", "88"):
    r = run(code)
    assert r["verdict"] == "KILL" and r["auto_kill_reason"] == "Public/conservation land", (code, r["verdict"], r["auto_kill_reason"])
for code in ("000", "001", "099", "080", None, ""):
    r = run(code)
    assert r["auto_kill_reason"] != "Public/conservation land", (code, r["auto_kill_reason"])
print(f"ok — '088' -> {run('088')['verdict']} {run('088')['score']}, '099' -> {run('099')['verdict']} {run('099')['score']}")
