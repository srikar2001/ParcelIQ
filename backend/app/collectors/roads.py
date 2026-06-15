"""
Source 08: Road Authority / Maintenance.

Queries by street name extracted from the parcel address. This is a
ROAD-LEVEL signal only — county maintenance of the named road does NOT
verify legal ingress/egress, frontage, easements, or title access for
THIS parcel. A spatial parcel-boundary-to-road-line comparison
(GeoPandas/Shapely) is listed as a future enhancement in the
implementation plan; this collector does not perform it.
"""
from __future__ import annotations

import re
from datetime import date

import httpx

from app.core.config import settings
from app.models.schema import RoadInfo, SourceEvidence


def _extract_street_name(address: str | None) -> str | None:
    """
    Strip the house number from an address to get the street name,
    e.g. "19931 Angel Ln" -> "ANGEL LN"
    """
    if not address:
        return None
    parts = address.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].upper().strip()


async def get_road_info(address: str | None) -> tuple[RoadInfo, SourceEvidence]:
    street = _extract_street_name(address)

    if not street:
        info = RoadInfo(found=False)
        evidence = SourceEvidence(
            source_id="08",
            source_name="Road Authority / Maintenance",
            pulled_or_searched_date=str(date.today()),
            status="no_address",
            confidence="not_validated",
            url=settings.roads_url,
            notes="No address available to derive street name.",
        )
        return info, evidence

    safe_street = re.sub(r"[^A-Z0-9 ]", "", street)

    params = {
        "where": f"hss_street = '{safe_street}'",
        "outFields": "street,authrty,owner,maintby,oneway,net_level",
        "returnGeometry": "false",
        "f": "json",
    }

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.roads_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        info = RoadInfo(found=False)
        evidence = SourceEvidence(
            source_id="08",
            source_name="Road Authority / Maintenance",
            pulled_or_searched_date=str(date.today()),
            status="no_match",
            confidence="not_validated",
            url=settings.roads_url,
            notes=(
                f"No road record found for street '{safe_street}'. "
                "Road-level signal only. Legal access/frontage not verified."
            ),
        )
        return info, evidence

    attrs = features[0]["attributes"]
    info = RoadInfo(
        found=True,
        street=attrs.get("street"),
        authority=attrs.get("authrty"),
        owner=attrs.get("owner"),
        maintained_by=attrs.get("maintby"),
    )

    evidence = SourceEvidence(
        source_id="08",
        source_name="Road Authority / Maintenance",
        pulled_or_searched_date=str(date.today()),
        status="partial_api",
        confidence="partial",
        url=settings.roads_url,
        raw=attrs,
        notes=(
            "Road-level county maintenance signal only. Does NOT verify legal "
            "ingress/egress, frontage, driveway permit, or easements/title for this parcel."
        ),
    )
    return info, evidence
