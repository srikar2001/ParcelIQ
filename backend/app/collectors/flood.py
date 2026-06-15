"""
Source 02: FEMA NFHL Flood Hazard Zones.

Point query at parcel centroid. Returns FLD_ZONE, ZONE_SUBTY, SFHA_TF, STATIC_BFE.

NOTE per master prompt: flood zone is NOT a wetlands/drainage/buildability
clearance. A Zone X result is a positive signal at the sampled point only.
"""
from __future__ import annotations

from datetime import date

import httpx

from app.core.config import settings
from app.models.schema import FloodInfo, SourceEvidence


async def get_flood_info(lat: float, lon: float) -> tuple[FloodInfo | None, SourceEvidence]:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE",
        "f": "json",
    }

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.fema_flood_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        evidence = SourceEvidence(
            source_id="02",
            source_name="FEMA NFHL Flood Hazard Zones",
            pulled_or_searched_date=str(date.today()),
            status="no_record_at_point",
            confidence="screening",
            url=settings.fema_flood_url,
            notes="No flood zone polygon returned at sampled point.",
        )
        return None, evidence

    attrs = features[0]["attributes"]
    sfha_raw = attrs.get("SFHA_TF")
    sfha = None
    if sfha_raw is not None:
        sfha = str(sfha_raw).upper() == "T"

    static_bfe = attrs.get("STATIC_BFE")
    if static_bfe == -9999:
        static_bfe = None

    info = FloodInfo(
        fld_zone=attrs.get("FLD_ZONE"),
        zone_subty=attrs.get("ZONE_SUBTY"),
        sfha=sfha,
        static_bfe=static_bfe,
    )

    evidence = SourceEvidence(
        source_id="02",
        source_name="FEMA NFHL Flood Hazard Zones",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.fema_flood_url,
        raw=attrs,
        notes="Flood zone is not wetland/drainage/buildability clearance.",
    )

    return info, evidence
