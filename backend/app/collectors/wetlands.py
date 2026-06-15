"""
Source 03: USFWS / NWI Wetlands (ESRI-hosted FeatureServer).

When parcel geometry rings are available, queries with the parcel bounding
box (esriSpatialRelIntersects) so edge-of-parcel wetlands are caught.
Falls back to centroid point if no geometry. Screening-level only —
polygon_acres is the mapped-polygon area, NOT parcel acreage. Professional
delineation is required to determine actual on-parcel wetland extent.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

import httpx

from app.core.config import settings
from app.models.schema import SourceEvidence, WetlandInfo


def _envelope_from_rings(rings: list) -> Optional[dict]:
    """Compute ESRI envelope dict from polygon rings in [lon, lat] pairs."""
    all_pts = [pt for ring in rings for pt in ring]
    if not all_pts:
        return None
    lons = [p[0] for p in all_pts]
    lats = [p[1] for p in all_pts]
    return {
        "xmin": min(lons),
        "ymin": min(lats),
        "xmax": max(lons),
        "ymax": max(lats),
        "spatialReference": {"wkid": 4326},
    }


async def get_wetland_info(
    lat: float,
    lon: float,
    geometry: Optional[dict] = None,
) -> tuple[WetlandInfo, SourceEvidence]:
    rings = geometry.get("rings") if geometry else None
    envelope = _envelope_from_rings(rings) if rings else None

    if envelope:
        params = {
            "geometry": json.dumps(envelope),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "ATTRIBUTE,WETLAND_TYPE,SYSTEM,CLASS,WATER_REGIME,Shape__Area",
            "f": "json",
        }
    else:
        params = {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "ATTRIBUTE,WETLAND_TYPE,SYSTEM,CLASS,WATER_REGIME,Shape__Area",
            "f": "json",
        }

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.nwi_wetlands_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        info = WetlandInfo(found=False)
        evidence = SourceEvidence(
            source_id="03",
            source_name="USFWS/NWI Wetlands",
            pulled_or_searched_date=str(date.today()),
            status="no_record_at_point",
            confidence="screening",
            url=settings.nwi_wetlands_url,
            notes="No mapped wetland polygon at sampled area. Does not prove absence of wetlands on parcel.",
        )
        return info, evidence

    # Use the largest polygon (most likely to be the dominant wetland type)
    attrs = max(
        (f["attributes"] for f in features),
        key=lambda a: a.get("Shape__Area") or 0,
    )

    info = WetlandInfo(
        found=True,
        attribute=attrs.get("ATTRIBUTE"),
        wetland_type=attrs.get("WETLAND_TYPE"),
        system=attrs.get("SYSTEM"),
        wetland_class=attrs.get("CLASS"),
        water_regime=attrs.get("WATER_REGIME"),
        polygon_acres=attrs.get("Shape__Area"),
    )

    evidence = SourceEvidence(
        source_id="03",
        source_name="USFWS/NWI Wetlands",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="screening",
        url=settings.nwi_wetlands_url,
        raw=attrs,
        notes=(
            f"{len(features)} wetland polygon(s) overlap parcel boundary. "
            "Polygon area is mapped-polygon size, not parcel acreage. Screening only."
        ),
    )

    return info, evidence
