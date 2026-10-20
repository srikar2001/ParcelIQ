"""
Offline check: a failed/timed-out kill-check (flood, wetlands, habitat) must
read as a data gap — never as a clean result or a positive.

  backend/venv/bin/python tests/test_scoring_data_gaps.py
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app.core.insight_engine import score_parcel  # noqa: E402
from app.routers import batch  # noqa: E402

ROADS = {"road_found": True, "road_surface": "paved", "road_private": False}
PARCEL = {"found": True, "acreage": 1.0, "building_count": 0}


def run(flood, wetlands, habitat):
    return score_parcel(flood, wetlands, habitat, batch._EPA_DEFAULT, ROADS, PARCEL, {})


clean = run({"zone": "X", "sfha": False},
            {"wetland_on_parcel": False, "wetland_nearby": False},
            {"habitat_found": False, "data_available": True})
gap = run(batch._FLOOD_DEFAULT, batch._WETLANDS_DEFAULT, batch._HABITAT_DEFAULT)

assert "No wetlands on parcel or nearby" in clean["positives"]
assert "No wetlands on parcel or nearby" not in gap["positives"], gap["positives"]
for f in ("Flood zone data unavailable", "Wetland data unavailable", "Wildlife check unavailable"):
    assert any(x.startswith(f) for x in gap["flags"]), (f, gap["flags"])
assert gap["sources_ok"] == clean["sources_ok"] - 3, (gap["sources_ok"], clean["sources_ok"])
assert gap["score"] <= clean["score"] - 9, (gap["score"], clean["score"])
print(f"ok — clean {clean['score']} ({clean['sources_ok']}/12) vs all-kill-checks-failed "
      f"{gap['score']} ({gap['sources_ok']}/12)")
