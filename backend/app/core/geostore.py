"""
Local Florida reference layers (wetlands, habitat, flood, easements,
waterways, powerlines) served from one SQLite file instead of Supabase.

The file is built by scripts/build_geostore.py from the old fl_* PostGIS
tables. Each lookup here reproduces the matching PostGIS RPC from
migrations/003-006 and returns the SAME row shape PostgREST returned, so the
collectors parse it unchanged:

    rows = await geostore.query("flood_zone_at", lat, lng)
    if rows is None:   # store not present -> caller falls back to the RPC

Lookups run in a worker thread (thread-local read-only connections); each is
an R-tree bbox probe plus a few shapely ops — typically 1-5 ms.
"""
from __future__ import annotations

import asyncio
import math
import os
import sqlite3
import threading
import zlib
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Point, box
from shapely.ops import nearest_points

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "geostore.sqlite"
PATH = Path(os.environ.get("GEOSTORE_PATH") or _DEFAULT_PATH)

_local = threading.local()
_status: dict = {"available": None}


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(f"file:{PATH}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        _local.conn = c
    return c


def available() -> bool:
    """True once the file exists and has every layer. Re-checked until found."""
    if _status["available"]:
        return True
    try:
        if not PATH.exists():
            _status.update(available=False, reason=f"missing {PATH}")
            return False
        meta = dict(_conn().execute("SELECT k, v FROM meta").fetchall())
        missing = [l for l in ("wetlands", "habitat", "flood", "easements", "waterways", "powerlines")
                   if f"rows_{l}" not in meta]
        if missing:
            _status.update(available=False, reason=f"layers missing: {missing}")
            return False
        _status.update(available=True, built_at=meta.get("built_at"),
                       size_mb=round(PATH.stat().st_size / 1e6))
        print(f"[GeoStore] using {PATH} (built {meta.get('built_at')})")
        return True
    except Exception as e:
        _status.update(available=False, reason=str(e))
        _local.conn = None
        return False


def status() -> dict:
    available()
    return dict(_status, path=str(PATH))


def _candidates(layer: str, x0: float, y0: float, x1: float, y1: float):
    """Rows whose bbox overlaps the query box -> (attrs list, geometry array)."""
    rows = _conn().execute(
        f"SELECT t.a, t.b, t.c, t.d, t.g FROM {layer}_idx i JOIN {layer} t ON t.id = i.id "
        f"WHERE i.minx <= ? AND i.maxx >= ? AND i.miny <= ? AND i.maxy >= ?",
        (x1, x0, y1, y0),
    ).fetchall()
    if not rows:
        return [], np.empty(0, dtype=object)
    geoms = shapely.from_wkb([zlib.decompress(r[4]) for r in rows])
    return [r[:4] for r in rows], geoms


# Local metric frame around the query point (WGS84 radii at that latitude).
# Over the <2 km distances these lookups use, this matches PostGIS geography
# distance to well under 0.1%.
def _meters_fn(lat: float, lng: float):
    a, e2 = 6378137.0, 0.00669437999014
    s = math.sin(math.radians(lat))
    n = a / math.sqrt(1 - e2 * s * s)
    m = a * (1 - e2) / (1 - e2 * s * s) ** 1.5
    kx = math.radians(1) * n * math.cos(math.radians(lat))
    ky = math.radians(1) * m

    def f(coords):
        out = np.empty_like(coords)
        out[:, 0] = (coords[:, 0] - lng) * kx
        out[:, 1] = (coords[:, 1] - lat) * ky
        return out
    return f


def _dist_m(geoms, lat: float, lng: float) -> np.ndarray:
    if len(geoms) == 0:
        return np.empty(0)
    projected = shapely.transform(geoms, _meters_fn(lat, lng))
    return shapely.distance(projected, Point(0, 0))


def _closest_point(geom, pt: Point) -> tuple[float, float]:
    p = nearest_points(geom, pt)[0]
    return p.y, p.x


# ── lookups (mirror migrations 003-006) ─────────────────────────────────────

def _wetland_near(lat, lng):
    pt = Point(lng, lat)
    box_on = box(lng - 0.00045, lat - 0.00045, lng + 0.00045, lat + 0.00045)
    box_near = box(lng - 0.001, lat - 0.001, lng + 0.001, lat + 0.001)
    attrs, geoms = _candidates("wetlands", *box_near.bounds)
    hit_on = shapely.intersects(geoms, box_on) if len(geoms) else np.zeros(0, bool)
    has_on = bool(hit_on.any())
    has_near = has_on or bool(len(geoms) and shapely.intersects(geoms, box_near).any())
    wt = wc = None
    if has_on:
        idx = np.flatnonzero(hit_on)
        best = idx[int(np.argmin(shapely.distance(geoms[idx], pt)))]
        wt, wc = attrs[best][0], attrs[best][1]
    return [{"on_parcel": has_on, "nearby": has_near, "wetland_type": wt, "wetland_code": wc}]


def _habitat_near(lat, lng):
    b = box(lng - 0.003, lat - 0.003, lng + 0.003, lat + 0.003)
    attrs, geoms = _candidates("habitat", *b.bounds)
    hit = np.flatnonzero(shapely.intersects(geoms, b)) if len(geoms) else []
    species = sorted({attrs[i][0] for i in hit if attrs[i][0]})
    return [{"found": len(hit) > 0, "species": species}]


def _flood_zone_at(lat, lng):
    pt = Point(lng, lat)
    attrs, geoms = _candidates("flood", lng, lat, lng, lat)
    hit = [i for i in np.flatnonzero(shapely.intersects(geoms, pt))] if len(geoms) else []
    if not hit:
        return []
    hit.sort(key=lambda i: (not bool(attrs[i][0]), attrs[i][2] != "T"))
    a, b, c, d = attrs[hit[0]]
    return [{"fld_zone": a, "zone_subty": b, "sfha_tf": c, "static_bfe": d}]


def _easement_near(lat, lng):
    pt = Point(lng, lat)
    attrs, geoms = _candidates("easements", lng, lat, lng, lat)
    for i in (np.flatnonzero(shapely.intersects(geoms, pt)) if len(geoms) else []):
        return [{"found": True, "program": attrs[i][0]}]
    return [{"found": False, "program": None}]


_WATER_RANK = {"canal": 1, "river": 2, "stream": 3, "drain": 4, "ditch": 5}


def _waterway_near(lat, lng):
    pt = Point(lng, lat)
    attrs, geoms = _candidates("waterways", lng - 0.0025, lat - 0.0025, lng + 0.0025, lat + 0.0025)
    if len(geoms) == 0:
        return [{"nearby": False, "waterway_type": None, "near_lat": None, "near_lng": None}]
    within = np.flatnonzero(_dist_m(geoms, lat, lng) <= 200)
    if len(within) == 0:
        return [{"nearby": False, "waterway_type": None, "near_lat": None, "near_lng": None}]
    best = min((attrs[i][0] for i in within), key=lambda w: _WATER_RANK.get(w, 6))
    best = best if best in _WATER_RANK else "waterway"
    nearest = within[int(np.argmin(shapely.distance(geoms[within], pt)))]
    near_lat, near_lng = _closest_point(geoms[nearest], pt)
    return [{"nearby": True, "waterway_type": best, "near_lat": near_lat, "near_lng": near_lng}]


def _power_near(lat, lng):
    pt = Point(lng, lat)
    attrs, geoms = _candidates("powerlines", lng - 0.018, lat - 0.018, lng + 0.018, lat + 0.018)
    if len(geoms):
        d = _dist_m(geoms, lat, lng)
        w500 = np.flatnonzero(d <= 500)
        if len(w500):
            nearest = w500[int(np.argmin(shapely.distance(geoms[w500], pt)))]
            near_lat, near_lng = _closest_point(geoms[nearest], pt)
            return [{"within_500": True, "within_1600": True, "near_lat": near_lat, "near_lng": near_lng}]
        if (d <= 1600).any():
            return [{"within_500": False, "within_1600": True, "near_lat": None, "near_lng": None}]
    return [{"within_500": False, "within_1600": False, "near_lat": None, "near_lng": None}]


_LOOKUPS = {
    "wetland_near": _wetland_near,
    "habitat_near": _habitat_near,
    "flood_zone_at": _flood_zone_at,
    "easement_near": _easement_near,
    "waterway_near": _waterway_near,
    "power_near": _power_near,
}


async def ensure_downloaded() -> None:
    """On a fresh Railway volume, fetch the file once from GEOSTORE_URL.

    GEOSTORE_TOKEN (optional) is sent as a Bearer token, which covers a private
    Hugging Face dataset or a GitHub release asset. A file that already exists
    with every layer is left alone, so redeploys don't re-download.
    """
    # GEOSTORE_URL may list several comma-separated parts (GitHub caps a
    # release asset at 2 GB); they're concatenated in order.
    urls = [u.strip() for u in os.environ.get("GEOSTORE_URL", "").split(",") if u.strip()]
    if not urls or available():
        return
    import hashlib
    import httpx
    headers = {"Accept": "application/octet-stream"}
    if os.environ.get("GEOSTORE_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GEOSTORE_TOKEN']}"
    want_sha = os.environ.get("GEOSTORE_SHA256", "").strip().lower()
    part = PATH.with_suffix(".part")
    PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"[GeoStore] downloading {len(urls)} part(s) -> {PATH}")
    try:
        sha = hashlib.sha256()
        # httpx drops the Authorization header on the cross-host redirect to
        # GitHub's storage, which is what the signed download URL expects.
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True) as c:
            with open(part, "wb") as f:
                for url in urls:
                    async with c.stream("GET", url, headers=headers) as r:
                        r.raise_for_status()
                        async for chunk in r.aiter_bytes(1 << 20):
                            f.write(chunk)
                            sha.update(chunk)
        if want_sha and sha.hexdigest() != want_sha:
            raise ValueError(f"checksum mismatch ({sha.hexdigest()[:12]}…)")
        part.replace(PATH)
        _local.conn = None
        print(f"[GeoStore] download complete, available={available()}")
    except Exception as e:
        part.unlink(missing_ok=True)
        _status.update(available=False, reason=f"download failed: {e}")
        print(f"[GeoStore] download failed: {e}")


def query_sync(name: str, lat: float, lng: float):
    return _LOOKUPS[name](float(lat), float(lng))


async def query(name: str, lat: float, lng: float):
    """RPC-shaped rows from the local store, or None if it isn't usable."""
    if not available():
        return None
    try:
        return await asyncio.to_thread(query_sync, name, lat, lng)
    except Exception as e:
        print(f"[GeoStore] {name} failed, falling back to Supabase: {e}")
        return None
