"""
Offline check for the nearest-parcel fallback (S8/S13):
- placeholder features (blank / REFERENCE ONLY) are skipped,
- point→polygon distance is right (inside = 0, outside ≈ metres),
- a nearest match scores as a caveat (-3 + 'Confirm the APN'), never the
  -5 'County data unavailable', and a government-owned NEAREST parcel does
  not auto-KILL (a point-intersect government parcel still does).

  backend/venv/bin/python tests/test_parcel_nearest.py
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app.collectors.parcel_fl import _dist_to_rings_m, _is_placeholder  # noqa: E402
from app.core.insight_engine import score_parcel  # noqa: E402

# placeholders
assert _is_placeholder({"PARCEL_ID": " ", "OWN_NAME": " "})
assert _is_placeholder({"PARCEL_ID": None})
assert _is_placeholder({"PARCEL_ID": "X1", "OWN_NAME": "REFERENCE ONLY"})
assert not _is_placeholder({"PARCEL_ID": "504210AF0010", "OWN_NAME": "S/OP ASSOCIATES"})

# distance: ~100 m square (lng,lat rings) around (28.5, -81.4)
lat, lng = 28.5, -81.4
d = 0.0005  # ≈ 49 m in x, 55 m in y
sq = [[[lng - d, lat - d], [lng + d, lat - d], [lng + d, lat + d], [lng - d, lat + d], [lng - d, lat - d]]]
assert _dist_to_rings_m(lat, lng, sq) == 0.0
east = _dist_to_rings_m(lat, lng + 2 * d, sq)  # point one half-width east of the edge
assert 45 < east < 52, east

FLOOD = {"zone": "X", "sfha": False}
WET = {"wetland_on_parcel": False, "wetland_nearby": True}  # -8, with dirt road keeps score < 100
HAB = {"habitat_found": False, "data_available": True}
EPA = {"contamination_found": False, "sites": []}
ROADS = {"road_found": True, "road_surface": "dirt", "road_private": False}  # dirt (-8) keeps the score off the 100 clamp


def run(**p):
    parcel = {"found": True, "acreage": 1.0, "building_count": 0, "land_use_code": "000", **p}
    return score_parcel(FLOOD, WET, HAB, EPA, ROADS, parcel, {})


point = run()
near = run(match="nearest", match_distance_m=12.4)
missing = score_parcel(FLOOD, WET, HAB, EPA, ROADS, {"found": False}, {})
assert near["score"] == point["score"] - 3, (point["score"], near["score"])
assert any("Confirm the APN" in f and "12 m" in f for f in near["flags"]), near["flags"]
assert not any("County data unavailable" in f for f in near["flags"])
assert any("County data unavailable" in f for f in missing["flags"])

gov_point = run(land_use_code="088")
gov_near = run(land_use_code="088", match="nearest", match_distance_m=5.6)
assert gov_point["verdict"] == "KILL" and gov_point["auto_kill_reason"] == "Public/conservation land"
assert gov_near["verdict"] == "PURSUE" and gov_near["auto_kill_reason"] != "Public/conservation land", gov_near
assert any("government-owned" in f for f in gov_near["flags"])
print(f"ok — point {point['score']}, nearest {near['score']}, gov point {gov_point['verdict']}, gov nearest {gov_near['verdict']} {gov_near['score']}")
