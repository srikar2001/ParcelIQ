"""
Sources 05 / 06: Zoning + Future Land Use.

Point query at parcel centroid. Zoning polygon "acres" field is the
district polygon area, not parcel acreage — deliberately excluded from
the model (see schema/UI mapping notes).
"""
from __future__ import annotations

from datetime import date

import httpx

from app.core.config import settings
from app.models.schema import FutureLandUseInfo, SourceEvidence, ZoningInfo


async def get_zoning_info(lat: float, lon: float) -> tuple[ZoningInfo | None, SourceEvidence]:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "nzone,nzone_desc,category",
        "f": "json",
    }

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.zoning_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        evidence = SourceEvidence(
            source_id="05",
            source_name="Hillsborough Zoning",
            pulled_or_searched_date=str(date.today()),
            status="no_record_at_point",
            confidence="screening",
            url=settings.zoning_url,
            notes="No zoning polygon returned at sampled point.",
        )
        return None, evidence

    attrs = features[0]["attributes"]
    info = ZoningInfo(
        nzone=attrs.get("nzone"),
        nzone_desc=attrs.get("nzone_desc"),
        category=attrs.get("category"),
    )

    evidence = SourceEvidence(
        source_id="05",
        source_name="Hillsborough Zoning",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.zoning_url,
        raw=attrs,
        notes="Zoning district polygon acreage excluded; not parcel acreage.",
    )

    return info, evidence


async def get_future_land_use(lat: float, lon: float) -> tuple[FutureLandUseInfo | None, SourceEvidence]:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLUE,FLU_DESC,JURISDICTION",
        "f": "json",
    }

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.future_lu_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        evidence = SourceEvidence(
            source_id="06",
            source_name="Future Land Use",
            pulled_or_searched_date=str(date.today()),
            status="no_record_at_point",
            confidence="screening",
            url=settings.future_lu_url,
            notes="No future land use polygon returned at sampled point.",
        )
        return None, evidence

    attrs = features[0]["attributes"]
    info = FutureLandUseInfo(
        flue=attrs.get("FLUE"),
        flu_desc=attrs.get("FLU_DESC"),
        jurisdiction=attrs.get("JURISDICTION"),
    )

    evidence = SourceEvidence(
        source_id="06",
        source_name="Future Land Use",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.future_lu_url,
        raw=attrs,
        notes="Planning category, distinct from zoning.",
    )

    return info, evidence
