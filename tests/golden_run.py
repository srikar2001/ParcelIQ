"""
Golden verdict set — screens ~40 fixed Florida coordinates through the real
backend pipeline (`_screen_coordinate`, no HTTP) and compares against
tests/golden.json to catch verdict/score drift.

  backend/venv/bin/python tests/golden_run.py            # compare vs golden.json
  backend/venv/bin/python tests/golden_run.py --snapshot # (re)write golden.json
  backend/venv/bin/python tests/golden_run.py --only 3,7 # subset by index

Exit code 1 when any point drifts (verdict change, |score delta| > SCORE_TOL,
parcel_id change) so it can gate a run. Source outages (sources_ok drop) are
reported separately: they explain drift but are not drift themselves.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
GOLDEN = Path(__file__).resolve().parent / "golden.json"
SCORE_TOL = 10
CONCURRENCY = 4

sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")  # explicit path — a bare load_dotenv() finds nothing

# (label, lat, lng) — spread across regions and land types on purpose.
POINTS = [
    ("Alachua rural NE Gainesville", 29.749272, -82.235482),
    ("Bradford rural Lawtey", 30.099676, -82.054001),
    ("Levy/Dixie Suwannee floodplain", 29.642166, -82.973691),
    ("Highlands Sebring subdivision", 27.515764, -81.530426),
    ("Martin Palm City", 27.180457, -80.393137),
    ("Miami-Dade Everglades / Big Cypress", 25.750000, -80.700000),
    ("Orange downtown Orlando", 28.538300, -81.379200),
    ("Miami Beach coastal", 25.790700, -80.130000),
    ("Monroe Key West", 24.555100, -81.780000),
    ("Leon downtown Tallahassee", 30.438300, -84.280700),
    ("Escambia Pensacola", 30.421300, -87.216900),
    ("Bay Panama City Beach", 30.176600, -85.805500),
    ("Marion horse country", 29.150000, -82.250000),
    ("Hendry Clewiston / Lake O", 26.750000, -80.930000),
    ("Lee Cape Coral NW lots", 26.700000, -82.000000),
    ("Lee Lehigh Acres lots", 26.620000, -81.620000),
    ("Brevard Palm Bay lots", 27.950000, -80.700000),
    ("Charlotte Port Charlotte", 26.980000, -82.100000),
    ("Marion Citrus Springs lots", 28.990000, -82.450000),
    ("Polk rural ridge", 27.900000, -81.600000),
    ("Hillsborough downtown Tampa", 27.950600, -82.457200),
    ("Pinellas St Petersburg", 27.767600, -82.640300),
    ("Duval downtown Jacksonville", 30.332200, -81.655700),
    ("Marion Ocala National Forest", 29.180000, -81.750000),
    ("Franklin Apalachicola", 29.725800, -84.983200),
    ("Suwannee Live Oak", 30.294900, -82.984000),
    ("Washington Chipley", 30.780000, -85.540000),
    ("Walton DeFuniak Springs", 30.720000, -86.120000),
    ("Okeechobee ranchland", 27.400000, -80.950000),
    ("Collier Immokalee", 26.420000, -81.420000),
    ("DeSoto Arcadia", 27.220000, -81.860000),
    ("Citrus Crystal River coast", 28.900000, -82.620000),
    ("Levy Cedar Key", 29.140000, -83.030000),
    ("Taylor Perry", 30.120000, -83.580000),
    ("Putnam Palatka", 29.650000, -81.640000),
    ("Volusia Deltona", 28.900000, -81.260000),
    ("Collier Naples", 26.140000, -81.790000),
    ("Broward Fort Lauderdale", 26.120000, -80.140000),
    ("Highlands Avon Park", 27.600000, -81.500000),
    ("Hernando Brooksville", 28.550000, -82.390000),
    ("Jackson Marianna", 30.770000, -85.230000),
    ("Brevard Kennedy Space Center (federal)", 28.570000, -80.650000),
]

KEEP_INFO = (
    "county", "parcel_id", "acreage", "land_use_code", "sources_ok", "sources_total",
    "flood_zone_subtype", "wetland_type", "road_distance_m", "evac_zone", "soil_drainage",
)


def _summarize(r: dict) -> dict:
    pi = r.get("parcel_info") or {}
    return {
        "verdict": r.get("verdict"),
        "score": r.get("score"),
        "auto_kill": r.get("auto_kill"),
        "auto_kill_reason": r.get("auto_kill_reason"),
        "kill_flags": r.get("kill_flags") or [],
        **{k: pi.get(k) for k in KEEP_INFO},
    }


async def _screen_all(points: list[tuple[int, tuple]]) -> dict[int, dict]:
    from app.routers.batch import _screen_coordinate

    sem = asyncio.Semaphore(CONCURRENCY)
    out: dict[int, dict] = {}

    async def one(i, label, lat, lng):
        async with sem:
            t0 = time.monotonic()
            try:
                r = await _screen_coordinate(label, {"formatted_address": label}, lat, lng)
                s = _summarize(r)
            except Exception as e:  # a crash is a finding, not a runner failure
                s = {"verdict": "CRASH", "error": f"{type(e).__name__}: {e}"}
            s["secs"] = round(time.monotonic() - t0, 1)
            out[i] = s
            print(f"  [{i:2}] {s.get('verdict', '?'):7} {str(s.get('score')):>4}  "
                  f"src {s.get('sources_ok')}/{s.get('sources_total')}  {s['secs']:5}s  {label}",
                  flush=True)

    await asyncio.gather(*(one(i, *p) for i, p in points))
    return out


def _compare(golden: dict, got: dict[int, dict]) -> int:
    drift, outages = [], []
    by_idx = {e["idx"]: e for e in golden["points"]}
    for i, now in sorted(got.items()):
        g = by_idx.get(i)
        if not g:
            continue
        was, label = g["result"], g["label"]
        why = []
        if now.get("verdict") != was.get("verdict"):
            why.append(f"verdict {was.get('verdict')} -> {now.get('verdict')}")
        if isinstance(now.get("score"), (int, float)) and isinstance(was.get("score"), (int, float)):
            d = now["score"] - was["score"]
            if abs(d) > SCORE_TOL:
                why.append(f"score {was['score']} -> {now['score']} ({d:+})")
        if was.get("parcel_id") and now.get("parcel_id") != was.get("parcel_id"):
            why.append(f"parcel_id {was.get('parcel_id')} -> {now.get('parcel_id')}")
        if (now.get("sources_ok") or 0) < (was.get("sources_ok") or 0):
            outages.append(f"[{i}] {label}: sources {was.get('sources_ok')} -> {now.get('sources_ok')}")
        if why:
            drift.append(f"[{i}] {label}: " + "; ".join(why))
    print(f"\n{len(got)} screened · {len(drift)} drifted · {len(outages)} with fewer sources")
    for line in drift:
        print("  DRIFT  " + line)
    for line in outages:
        print("  SOURCE " + line)
    return 1 if drift else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true", help="rewrite golden.json from a fresh run")
    ap.add_argument("--only", help="comma-separated indexes to run")
    ap.add_argument("--allow-degraded", action="store_true",
                    help="snapshot even without a local geostore (results will be outage-mode)")
    a = ap.parse_args()

    # Since 2026-10-20 the flood/wetland/habitat/easement/OSM layers live ONLY in
    # geostore.sqlite (the Supabase fl_* RPCs 404). Without a local copy every
    # point runs in outage mode, so a snapshot would be a meaningless baseline.
    from app.core import geostore
    print(f"geostore: {geostore.status()}")
    if a.snapshot and not geostore.available() and not a.allow_degraded:
        print("refusing to snapshot: no local geostore (set GEOSTORE_PATH) — pass --allow-degraded to override")
        return 2

    idx = [int(x) for x in a.only.split(",")] if a.only else list(range(len(POINTS)))
    pts = [(i, POINTS[i]) for i in idx]
    print(f"Screening {len(pts)} golden points (concurrency {CONCURRENCY})…")
    t0 = time.monotonic()
    got = asyncio.run(_screen_all(pts))
    print(f"total {time.monotonic() - t0:.0f}s")

    if a.snapshot:
        doc = {
            "created": time.strftime("%Y-%m-%d"),
            "score_tolerance": SCORE_TOL,
            "points": [
                {"idx": i, "label": l, "lat": la, "lng": ln, "result": got[i]}
                for i, (l, la, ln) in pts
            ],
        }
        GOLDEN.write_text(json.dumps(doc, indent=1) + "\n")
        print(f"wrote {GOLDEN.relative_to(ROOT)}")
        return 0
    if not GOLDEN.exists():
        print("no tests/golden.json yet — run with --snapshot first")
        return 2
    return _compare(json.loads(GOLDEN.read_text()), got)


if __name__ == "__main__":
    sys.exit(main())
