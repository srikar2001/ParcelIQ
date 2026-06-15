"""
Sources 07A / 07B: Water + Sewer Service Areas.

Point query at parcel centroid. A service-area match is a positive
signal only — it does NOT guarantee an active connection, meter, main,
or any particular connection cost. Deduplicate by serviceby field since
overlapping records are common.
"""
from __future__ import annotations

from datetime import date

import httpx

from app.core.config import settings
from app.models.schema import ServiceAreaInfo, SourceEvidence


async def _query_service_area(url: str, lat: float, lon: float) -> tuple[list[dict], dict | None]:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "serviceby",
        "f": "json",
    }

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    attrs_list = [f["attributes"] for f in features]
    raw = attrs_list[0] if attrs_list else None
    return attrs_list, raw


async def get_water_service(lat: float, lon: float) -> tuple[ServiceAreaInfo, SourceEvidence]:
    attrs_list, raw = await _query_service_area(settings.water_service_url, lat, lon)

    if not attrs_list:
        info = ServiceAreaInfo(found=False)
        evidence = SourceEvidence(
            source_id="07A",
            source_name="Water Service Area",
            pulled_or_searched_date=str(date.today()),
            status="no_record_at_point",
            confidence="screening",
            url=settings.water_service_url,
            notes="No water service area record at sampled point.",
        )
        return info, evidence

    # Deduplicate by serviceby
    service_by_values = {a.get("serviceby") for a in attrs_list}
    service_by = next(iter(service_by_values))

    info = ServiceAreaInfo(found=True, service_by=service_by)
    evidence = SourceEvidence(
        source_id="07A",
        source_name="Water Service Area",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.water_service_url,
        raw=raw,
        notes=(
            f"{len(attrs_list)} overlapping record(s) deduplicated. "
            "Service area found; active connection/meter/main/cost not verified."
        ),
    )
    return info, evidence


async def get_sewer_service(lat: float, lon: float) -> tuple[ServiceAreaInfo, SourceEvidence]:
    attrs_list, raw = await _query_service_area(settings.sewer_service_url, lat, lon)

    if not attrs_list:
        info = ServiceAreaInfo(found=False)
        evidence = SourceEvidence(
            source_id="07B",
            source_name="Sewer Service Area",
            pulled_or_searched_date=str(date.today()),
            status="no_record_at_point",
            confidence="screening",
            url=settings.sewer_service_url,
            notes="No sewer service area record at sampled point.",
        )
        return info, evidence

    service_by_values = {a.get("serviceby") for a in attrs_list}
    service_by = next(iter(service_by_values))

    info = ServiceAreaInfo(found=True, service_by=service_by)
    evidence = SourceEvidence(
        source_id="07B",
        source_name="Sewer Service Area",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.sewer_service_url,
        raw=raw,
        notes=(
            f"{len(attrs_list)} overlapping record(s) deduplicated. "
            "Service area found; active connection/main/cost not verified."
        ),
    )
    return info, evidence
