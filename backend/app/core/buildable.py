"""
Buildable acreage: how much of a parcel is NOT under mapped wetlands or a
FEMA Special Flood Hazard Area.

Pure local computation over the geostore polygons (NWI wetlands + FEMA NFHL
flood) intersected with the parcel boundary — no upstream calls, ~5-50 ms.

    compute_sync(rings)   # rings = [[[lat, lng], ...], ...]  (parcel_info.geometry)

Areas are measured in a local metric frame around the parcel (the same
WGS84-radii projection geostore uses for distances), accurate to well under
1% at parcel scale.
"""
from __future__ import annotations

import asyncio
import math

import shapely
from shapely.geometry import Polygon
from shapely.validation import make_valid

from app.core import geostore

M2_PER_ACRE = 4046.8564224
MAX_RINGS = 50
MAX_VERTICES = 20_000
MAX_ACRES = 5_000          # bigger than any lot an investor screens here
_OVERLAY_TOL_DEG = 0.000004  # ~0.4 m simplify for the map overlay
_OVERLAY_MAX_PTS = 4_000


def _ring_polys(rings) -> list[Polygon]:
    polys = []
    for ring in rings[:MAX_RINGS]:
        pts = []
        for p in ring if isinstance(ring, list) else []:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                try:
                    lat, lng = float(p[0]), float(p[1])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180:
                    pts.append((lng, lat))
        if len(pts) >= 3:
            poly = make_valid(Polygon(pts))
            if not poly.is_empty and poly.area > 0:
                polys.append(poly)
    return polys


def parcel_shape(rings):
    """Esri rings (outer + holes, possibly multipart) -> one valid geometry.

    XOR-ing the rings gives the right answer for both holes and separate
    parts without relying on ring orientation, which the converter loses.
    """
    if not isinstance(rings, list):
        return None
    if sum(len(r) for r in rings[:MAX_RINGS] if isinstance(r, list)) > MAX_VERTICES:
        raise ValueError("Parcel boundary has too many vertices.")
    polys = _ring_polys(rings)
    if not polys:
        return None
    shape = polys[0]
    for p in polys[1:]:
        shape = shape.symmetric_difference(p)
    shape = make_valid(shape)
    return None if shape.is_empty or shape.area == 0 else shape


def _union_hits(layer: str, shape, keep=None):
    """Union of the layer's polygons that intersect `shape`, clipped to it,
    plus per-label clipped geometries (label = attr a)."""
    attrs, geoms = geostore._candidates(layer, *shape.bounds)
    if len(geoms) == 0:
        return None, {}
    hit = shapely.intersects(geoms, shape)
    by_label: dict[str, list] = {}
    for i in hit.nonzero()[0]:
        if keep and not keep(attrs[i]):
            continue
        g = make_valid(geoms[i])
        by_label.setdefault(attrs[i][0] or "Unlabeled", []).append(g)
    if not by_label:
        return None, {}
    clipped = {k: shapely.intersection(shapely.union_all(v), shape) for k, v in by_label.items()}
    clipped = {k: v for k, v in clipped.items() if not v.is_empty}
    if not clipped:
        return None, {}
    return shapely.union_all(list(clipped.values())), clipped


def _is_sfha(a) -> bool:
    zone, sfha = (a[0] or "").upper(), (a[2] or "").upper()
    return sfha == "T" or zone.startswith(("A", "V"))


def _polys_latlng(geom, budget: list[int]) -> list:
    """Polygonal geometry -> Leaflet multipolygon [[outer, hole, ...], ...]
    with [lat, lng] points, simplified and capped at a total point budget."""
    out = []
    if geom is None or geom.is_empty:
        return out
    g = shapely.simplify(geom, _OVERLAY_TOL_DEG, preserve_topology=True)
    for part in getattr(g, "geoms", [g]):
        if part.geom_type in ("GeometryCollection", "MultiPolygon"):
            out += _polys_latlng(part, budget)
            continue
        if part.geom_type != "Polygon" or part.is_empty:
            continue
        rings = [[[round(y, 7), round(x, 7)] for x, y in r.coords] for r in [part.exterior, *part.interiors]]
        n = sum(len(r) for r in rings)
        if budget[0] - n < 0:
            break
        budget[0] -= n
        out.append(rings)
    return out


def compute_sync(rings) -> dict:
    shape = parcel_shape(rings)
    if shape is None:
        return {"ok": False, "reason": "no_boundary",
                "message": "No parcel boundary on file, so the buildable area can't be measured."}
    c = shape.centroid
    to_m = geostore._meters_fn(c.y, c.x)

    def acres(g) -> float:
        if g is None or g.is_empty:
            return 0.0
        return float(shapely.area(shapely.transform(g, to_m))) / M2_PER_ACRE

    total = acres(shape)
    if total > MAX_ACRES:
        return {"ok": False, "reason": "too_large",
                "message": f"Parcel is {total:,.0f} ac; buildable area is measured up to {MAX_ACRES:,} ac."}
    if total <= 0:
        return {"ok": False, "reason": "no_boundary", "message": "Parcel boundary has no area."}
    if not geostore.available():
        return {"ok": False, "reason": "unavailable",
                "message": "Wetland and flood maps are temporarily unavailable."}

    wet, wet_by = _union_hits("wetlands", shape)
    flood, flood_by = _union_hits("flood", shape, keep=_is_sfha)
    constrained = shapely.union_all([g for g in (wet, flood) if g is not None]) if (wet or flood) else None
    clear = shape.difference(constrained) if constrained is not None else shape

    wet_ac, flood_ac = acres(wet), acres(flood)
    con_ac = min(acres(constrained), total)
    clear_ac = max(total - con_ac, 0.0)
    flood_only_ac = max(con_ac - wet_ac, 0.0)   # flood area not already counted as wetland
    largest = max((acres(p) for p in getattr(clear, "geoms", [clear]) if p.geom_type == "Polygon"), default=0.0)

    def r2(x):
        return round(x, 2)

    def breakdown(by):
        rows = sorted(((k, acres(v)) for k, v in by.items()), key=lambda kv: -kv[1])
        return [{"label": k, "acres": r2(a), "pct": round(100 * a / total, 1)} for k, a in rows if a >= 0.005][:5]

    budget = [_OVERLAY_MAX_PTS]
    return {
        "ok": True,
        "mapped_acres": r2(total),
        "clear_acres": r2(clear_ac),
        "clear_pct": round(100 * clear_ac / total, 1),
        "largest_clear_acres": r2(largest),
        "wetland_acres": r2(wet_ac),
        "wetland_pct": round(100 * wet_ac / total, 1),
        "flood_acres": r2(flood_ac),
        "flood_pct": round(100 * flood_ac / total, 1),
        "flood_only_acres": r2(flood_only_ac),
        "wetland_types": breakdown(wet_by),
        "flood_zones": breakdown(flood_by),
        "overlay": {
            "wetland": _polys_latlng(wet, budget),
            "flood": _polys_latlng(flood.difference(wet) if (flood is not None and wet is not None) else flood, budget),
        },
        "sources": ["USFWS National Wetlands Inventory", "FEMA National Flood Hazard Layer (SFHA)"],
    }


async def compute(rings) -> dict:
    return await asyncio.to_thread(compute_sync, rings)
