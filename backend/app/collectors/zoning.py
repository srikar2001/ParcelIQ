"""
Sources 05 / 06: Zoning + Future Land Use.

Uses parcel polygon envelope intersection when geometry available;
falls back to centroid point. Zoning polygon "acres" field is the
district polygon area, not parcel acreage — deliberately excluded.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

import httpx

from app.core.config import settings
from app.models.schema import FutureLandUseInfo, SourceEvidence, ZoningInfo


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


def _build_params(lat: float, lon: float, geometry: Optional[dict], out_fields: str) -> tuple[dict, str]:
    rings = geometry.get("rings") if geometry else None
    envelope = _envelope_from_rings(rings) if rings else None
    if envelope:
        return {
            "geometry": json.dumps(envelope),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": out_fields,
            "f": "json",
        }, "polygon envelope intersection"
    return {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": out_fields,
        "f": "json",
    }, "centroid point"


async def get_zoning_info(
    lat: float,
    lon: float,
    geometry: Optional[dict] = None,
) -> tuple[ZoningInfo | None, SourceEvidence]:
    params, query_desc = _build_params(lat, lon, geometry, "nzone,nzone_desc,category")

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.zoning_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        return None, SourceEvidence(
            source_id="05",
            source_name="Hillsborough Zoning",
            pulled_or_searched_date=str(date.today()),
            status="no_record_at_point",
            confidence="screening",
            url=settings.zoning_url,
            query_used=query_desc,
            notes=f"No zoning polygon returned ({query_desc}).",
            public_verify_url="https://gis.hillsboroughcounty.org/",
        )

    attrs = features[0]["attributes"]
    return ZoningInfo(
        nzone=attrs.get("nzone"),
        nzone_desc=attrs.get("nzone_desc"),
        category=attrs.get("category"),
    ), SourceEvidence(
        source_id="05",
        source_name="Hillsborough Zoning",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.zoning_url,
        raw=attrs,
        query_used=query_desc,
        notes="Zoning district polygon acreage excluded — not parcel acreage.",
        public_verify_url="https://gis.hillsboroughcounty.org/",
    )


async def get_future_land_use(
    lat: float,
    lon: float,
    geometry: Optional[dict] = None,
) -> tuple[FutureLandUseInfo | None, SourceEvidence]:
    params, query_desc = _build_params(lat, lon, geometry, "FLUE,FLU_DESC,JURISDICTION")

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.future_lu_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        return None, SourceEvidence(
            source_id="06",
            source_name="Future Land Use",
            pulled_or_searched_date=str(date.today()),
            status="no_record_at_point",
            confidence="screening",
            url=settings.future_lu_url,
            query_used=query_desc,
            notes=f"No future land use polygon returned ({query_desc}).",
            public_verify_url="https://gis.hillsboroughcounty.org/",
        )

    attrs = features[0]["attributes"]
    return FutureLandUseInfo(
        flue=attrs.get("FLUE"),
        flu_desc=attrs.get("FLU_DESC"),
        jurisdiction=attrs.get("JURISDICTION"),
    ), SourceEvidence(
        source_id="06",
        source_name="Future Land Use",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.future_lu_url,
        raw=attrs,
        query_used=query_desc,
        notes="Planning category, distinct from zoning.",
        public_verify_url="https://gis.hillsboroughcounty.org/",
    )
