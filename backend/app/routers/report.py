"""
Report endpoint: orchestrates the full ParcelIQ pipeline for ANY
Hillsborough folio (per master prompt — demo property is not hardcoded).

Pipeline:
  1. Get parcel attributes (01) + geometry/centroid (04)
  2. Get address point confirmation (12)
  3. Run environmental collectors at centroid (02 flood, 03 wetlands, 11 evac)
  4. Run zoning/FLU collectors at centroid (05, 06)
  5. Run utilities collectors at centroid (07A, 07B)
  6. Run road collector (08)
  7. Run permits/CO collectors (09, 10)
  8. Assemble PropertyReport + attach manual sources (13-18)
  9. Run Insight Engine -> BuyerInsightsReport
  10. Return both
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.collectors import (
    address_point,
    evac,
    flood,
    parcel,
    permits,
    roads,
    utilities,
    wetlands,
    zoning,
)
from app.core.insight_engine import run_insight_engine
from app.models.schema import (
    BuyerInsightsReport,
    EnvironmentalSection,
    PropertyReport,
    SourceEvidence,
    UtilitiesSection,
)

router = APIRouter(prefix="/api", tags=["report"])

MANUAL_SOURCES_PATH = Path(__file__).resolve().parents[2] / "data" / "manual_sources.json"


def _load_manual_sources() -> list[SourceEvidence]:
    if not MANUAL_SOURCES_PATH.exists():
        return []
    raw = json.loads(MANUAL_SOURCES_PATH.read_text())
    entries = []
    for item in raw.get("manual_sources", []):
        entries.append(
            SourceEvidence(
                source_id=item["source_id"],
                source_name=item["source_name"],
                pulled_or_searched_date="not_pulled",
                status=item["status"],
                confidence=item["confidence"],
                url=item.get("url"),
                notes=item.get("notes"),
            )
        )
    return entries


@router.get("/report/{folio}")
async def get_report(folio: str):
    # ── Step 1: Parcel attributes + geometry
    summary, attr_evidence = await parcel.get_parcel_attributes(folio)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"No parcel found for FOLIO {folio}")

    geometry, centroid, geom_evidence = await parcel.get_parcel_geometry(folio)
    summary.geometry = geometry
    summary.centroid = centroid

    source_evidence: list[SourceEvidence] = [e for e in (attr_evidence, geom_evidence) if e]

    if centroid is None:
        # Without a centroid we can't run point-based collectors.
        # Still return what we have rather than failing entirely.
        report = PropertyReport(
            property=summary,
            environmental=EnvironmentalSection(),
            manual_sources=_load_manual_sources(),
            source_evidence=source_evidence,
        )
        insights, buildability = run_insight_engine(report)
        return {
            "property_report": report.model_dump(),
            "buyer_insights": BuyerInsightsReport(
                folio=folio, buildability=buildability, top_findings=insights
            ).model_dump(),
        }

    lat, lon = centroid["lat"], centroid["lon"]

    # ── Step 2: Address point confirmation
    addr_attrs, addr_evidence = await address_point.get_address_point(summary.address)
    source_evidence.append(addr_evidence)
    if addr_attrs:
        # Use official STRAP/FOLIO to cross-check, but don't overwrite
        # HCPA values — just attach as evidence.
        pass

    # ── Steps 3-7: run remaining collectors concurrently
    # Pass polygon geometry to flood/zoning so intersection uses parcel envelope
    results = await asyncio.gather(
        flood.get_flood_info(lat, lon, geometry),
        wetlands.get_wetland_info(lat, lon, geometry),
        evac.get_evac_zone_info(lat, lon),
        zoning.get_zoning_info(lat, lon, geometry),
        zoning.get_future_land_use(lat, lon, geometry),
        utilities.get_water_service(lat, lon),
        utilities.get_sewer_service(lat, lon),
        roads.get_road_info(summary.address),
        permits.get_permits_info(summary.address, folio),
        return_exceptions=True,
    )

    (
        flood_result,
        wetland_result,
        evac_result,
        zoning_result,
        flu_result,
        water_result,
        sewer_result,
        road_result,
        permits_result,
    ) = results

    def unpack(result, default_count=2):
        """Unpack (value, evidence) tuple, handling exceptions gracefully."""
        if isinstance(result, Exception):
            return None, None
        return result

    flood_info, flood_evidence = unpack(flood_result)
    wetland_info, wetland_evidence = unpack(wetland_result)
    evac_info, evac_evidence = unpack(evac_result)
    zoning_info, zoning_evidence = unpack(zoning_result)
    flu_info, flu_evidence = unpack(flu_result)
    water_info, water_evidence = unpack(water_result)
    sewer_info, sewer_evidence = unpack(sewer_result)
    road_info, road_evidence = unpack(road_result)

    if isinstance(permits_result, Exception):
        permits_info, permits_evidence_list = None, []
    else:
        permits_info, permits_evidence_list = permits_result

    for ev in (
        flood_evidence,
        wetland_evidence,
        evac_evidence,
        zoning_evidence,
        flu_evidence,
        water_evidence,
        sewer_evidence,
        road_evidence,
        *permits_evidence_list,
    ):
        if ev:
            source_evidence.append(ev)

    environmental = EnvironmentalSection(
        flood=flood_info,
        wetlands=wetland_info,
        evacuation_zone=evac_info,
    )

    utilities_section = UtilitiesSection(
        water_service_area=water_info,
        sewer_service_area=sewer_info,
    )

    report = PropertyReport(
        property=summary,
        environmental=environmental,
        zoning=zoning_info,
        future_land_use=flu_info,
        utilities=utilities_section,
        road=road_info,
        permits=permits_info,
        manual_sources=_load_manual_sources(),
        source_evidence=source_evidence,
    )

    # ── Step 9: Insight Engine
    insights, buildability = run_insight_engine(report)

    buyer_insights = BuyerInsightsReport(
        folio=folio,
        buildability=buildability,
        top_findings=insights,
    )

    return {
        "property_report": report.model_dump(),
        "buyer_insights": buyer_insights.model_dump(),
    }
