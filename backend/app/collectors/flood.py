"""
Source 02: FEMA NFHL Flood Hazard Zones.

When parcel geometry rings are available, queries with the parcel bounding
box (esriSpatialRelIntersects) so parcels that straddle zone boundaries are
caught correctly. Falls back to centroid point when no geometry provided.

NOTE: flood zone result is not a wetland, drainage, or buildability clearance.
A Zone X result is a positive signal at the sampled area only.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

import httpx

from app.core.config import settings
from app.models.schema import FloodInfo, SourceEvidence


def _envelope_from_rings(rings: list) -> Optional[dict]:
    all_pts = [pt for ring in rings for pt in ring]
    if not all_pts:
        return None
    lons = [p[0] for p in all_pts]
    lats = [p[1] for p in all_pts]
    return {
        "xmin": min(lons), "ymin": min(lats),
        "xmax": max(lons), "ymax": max(lats),
        "spatialReference": {"wkid": 4326},
    }


def _zone_risk(attrs: dict) -> int:
    z = (attrs.get("FLD_ZONE") or "").upper()
    if z.startswith("V"): return 3
    if z.startswith("A"): return 2
    if z == "X": return 0
    return 1


async def get_flood_info(
    lat: float,
    lon: float,
    geometry: Optional[dict] = None,
) -> tuple[FloodInfo | None, SourceEvidence]:
    rings = geometry.get("rings") if geometry else None
    envelope = _envelope_from_rings(rings) if rings else None

    if envelope:
        params = {
            "geometry": json.dumps(envelope),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE",
            "f": "json",
        }
        query_desc = "polygon envelope intersection"
    else:
        params = {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE",
            "f": "json",
        }
        query_desc = "centroid point"

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.fema_flood_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        return None, SourceEvidence(
            source_id="02",
            source_name="FEMA NFHL Flood Hazard Zones",
            pulled_or_searched_date=str(date.today()),
            status="no_record_at_point",
            confidence="screening",
            url=settings.fema_flood_url,
            notes=f"No flood zone polygon returned ({query_desc}). Does not mean no flood risk.",
            public_verify_url="https://msc.fema.gov/portal/search",
        )

    # When multiple zones returned (parcel straddles boundary), report highest risk
    attrs = max((f["attributes"] for f in features), key=_zone_risk)
    sfha_raw = attrs.get("SFHA_TF")
    sfha = None
    if sfha_raw is not None:
        sfha = str(sfha_raw).upper() in ("T", "TRUE", "1")

    static_bfe = attrs.get("STATIC_BFE")
    if static_bfe == -9999:
        static_bfe = None

    zone_count = len(features)
    notes = (
        f"Queried via {query_desc}. "
        f"{zone_count} flood zone polygon(s) intersected. "
        + ("Multiple zones — highest risk zone shown. " if zone_count > 1 else "")
        + "Zone result is not wetland/drainage/buildability clearance."
    )

    return FloodInfo(
        fld_zone=attrs.get("FLD_ZONE"),
        zone_subty=attrs.get("ZONE_SUBTY"),
        sfha=sfha,
        static_bfe=static_bfe,
    ), SourceEvidence(
        source_id="02",
        source_name="FEMA NFHL Flood Hazard Zones",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.fema_flood_url,
        raw=attrs,
        notes=notes,
        public_verify_url="https://msc.fema.gov/portal/search",
    )
