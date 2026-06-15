"""
Source 11: Evacuation Zone.

Point query. IMPORTANT: a "no records" result means no evacuation zone
returned at the sampled point. It does NOT mean "no hurricane risk" —
never phrase it that way.
"""
from __future__ import annotations

from datetime import date

import httpx

from app.core.config import settings
from app.models.schema import EvacZoneInfo, SourceEvidence


async def get_evac_zone_info(lat: float, lon: float) -> tuple[EvacZoneInfo, SourceEvidence]:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "zone",
        "f": "json",
    }

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.evac_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        info = EvacZoneInfo(found=False)
        evidence = SourceEvidence(
            source_id="11",
            source_name="Evacuation Zone",
            pulled_or_searched_date=str(date.today()),
            status="no_record_at_point",
            confidence="screening",
            url=settings.evac_url,
            notes="No evacuation zone record returned at sampled point. Does not mean no hurricane/storm risk.",
        )
        return info, evidence

    attrs = features[0]["attributes"]
    info = EvacZoneInfo(found=True, zone=attrs.get("zone"))

    evidence = SourceEvidence(
        source_id="11",
        source_name="Evacuation Zone",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.evac_url,
        raw=attrs,
    )

    return info, evidence
