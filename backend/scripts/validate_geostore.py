"""
Compare every local geostore lookup against the live Supabase RPC it replaces.

    GEOSTORE_PATH=... python scripts/validate_geostore.py [points_per_layer]

Samples random Florida points plus points right next to real features of each
layer (so hits, misses and edges are all exercised), runs both sides, and
prints any disagreement. Exit code 1 if anything differs.
"""
from __future__ import annotations

import asyncio
import os
import random
import sqlite3
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.core import geostore  # noqa: E402

URL = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/rpc/"
KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ["SUPABASE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

LAYER_FOR = {"wetland_near": "wetlands", "habitat_near": "habitat", "flood_zone_at": "flood",
             "easement_near": "easements", "waterway_near": "waterways", "power_near": "powerlines"}


def sample_points(n: int) -> list[tuple[float, float]]:
    rnd = random.Random(7)
    pts = [(rnd.uniform(24.6, 30.9), rnd.uniform(-87.5, -80.1)) for _ in range(n)]
    db = sqlite3.connect(f"file:{geostore.PATH}?mode=ro", uri=True)
    for layer in set(LAYER_FOR.values()):
        total = db.execute(f"SELECT max(id) FROM {layer}_idx").fetchone()[0]
        for _ in range(n):
            r = db.execute(f"SELECT minx, maxx, miny, maxy FROM {layer}_idx WHERE id = ?",
                           (rnd.randint(1, total),)).fetchone()
            if not r:
                continue
            j = rnd.choice([0, 0.0003, 0.0008, 0.002, 0.005])
            pts.append(((r[2] + r[3]) / 2 + rnd.uniform(-j, j), (r[0] + r[1]) / 2 + rnd.uniform(-j, j)))
    return pts


def norm(name: str, rows):
    row = (rows[0] if rows else {}) if isinstance(rows, list) else (rows or {})
    row = dict(row)
    if name == "habitat_near":
        row["species"] = sorted(row.get("species") or [])
    for k in ("near_lat", "near_lng"):
        if row.get(k) is not None:
            row[k] = round(row[k], 5)
    if name == "flood_zone_at" and row.get("static_bfe") is not None:
        row["static_bfe"] = float(row["static_bfe"])
    return row


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    assert geostore.available(), geostore.status()
    pts = sample_points(n)
    sem = asyncio.Semaphore(4)
    diffs, checked, errors = [], 0, []

    async with httpx.AsyncClient(timeout=30) as c:
        async def one(name, lat, lng):
            nonlocal checked
            remote = None
            async with sem:
                for attempt in range(3):
                    r = await c.post(URL + name, json={"in_lat": lat, "in_lng": lng}, headers=H)
                    if r.status_code == 200:
                        remote = norm(name, r.json())
                        break
                    await asyncio.sleep(1 + attempt)
            if remote is None:   # Supabase itself failed (timeout) — not a mismatch
                errors.append((name, lat, lng, r.status_code))
                return
            local = norm(name, geostore.query_sync(name, lat, lng))
            checked += 1
            if local != remote:
                diffs.append((name, lat, lng, local, remote))

        await asyncio.gather(*[one(name, lat, lng) for lat, lng in pts for name in LAYER_FOR])

    print(f"checked {checked} lookups over {len(pts)} points; {len(diffs)} differ; "
          f"{len(errors)} skipped (Supabase RPC errored)")
    for d in diffs[:40]:
        print("DIFF", d)
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    asyncio.run(main())
