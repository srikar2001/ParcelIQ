"""
GET /api/narrative/{folio}

Calls the full report pipeline (same as /api/report/{folio}) then passes
the result to the Claude narrative generator. Designed to be called async
from the frontend after the main report renders, so the AI briefing loads
without blocking the report.
"""
from __future__ import annotations

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
from app.core.narrative import generate_narrative
from app.models.schema import (
    BuyerInsightsReport,
    EnvironmentalSection,
    PropertyReport,
    UtilitiesSection,
)
from app.routers.report import _load_manual_sources
import asyncio

router = APIRouter(prefix="/api", tags=["narrative"])


@router.get("/narrative/{folio}")
async def get_narrative(folio: str):
    summary, attr_evidence = await parcel.get_parcel_attributes(folio)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"No parcel found for FOLIO {folio}")

    geometry, centroid, geom_evidence = await parcel.get_parcel_geometry(folio)
    summary.geometry = geometry
    summary.centroid = centroid

    source_evidence = [e for e in (attr_evidence, geom_evidence) if e]

    if centroid is None:
        raise HTTPException(status_code=422, detail="No geometry available for narrative generation")

    lat, lon = centroid["lat"], centroid["lon"]

    _, addr_evidence = await address_point.get_address_point(summary.address)
    source_evidence.append(addr_evidence)

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
        flood_r, wetland_r, evac_r, zoning_r, flu_r,
        water_r, sewer_r, road_r, permits_r,
    ) = results

    def _unpack(r):
        return (None, None) if isinstance(r, Exception) else r

    flood_info,   flood_ev   = _unpack(flood_r)
    wetland_info, wetland_ev = _unpack(wetland_r)
    evac_info,    evac_ev    = _unpack(evac_r)
    zoning_info,  zoning_ev  = _unpack(zoning_r)
    flu_info,     flu_ev     = _unpack(flu_r)
    water_info,   water_ev   = _unpack(water_r)
    sewer_info,   sewer_ev   = _unpack(sewer_r)
    road_info,    road_ev    = _unpack(road_r)

    if isinstance(permits_r, Exception):
        permits_info, permits_evs = None, []
    else:
        permits_info, permits_evs = permits_r

    for ev in (flood_ev, wetland_ev, evac_ev, zoning_ev, flu_ev, water_ev, sewer_ev, road_ev, *permits_evs):
        if ev:
            source_evidence.append(ev)

    report = PropertyReport(
        property=summary,
        environmental=EnvironmentalSection(flood=flood_info, wetlands=wetland_info, evacuation_zone=evac_info),
        zoning=zoning_info,
        future_land_use=flu_info,
        utilities=UtilitiesSection(water_service_area=water_info, sewer_service_area=sewer_info),
        road=road_info,
        permits=permits_info,
        manual_sources=_load_manual_sources(),
        source_evidence=source_evidence,
    )

    insights, buildability = run_insight_engine(report)
    buyer_insights = BuyerInsightsReport(folio=folio, buildability=buildability, top_findings=insights)

    narrative_text = await generate_narrative(report, buyer_insights)

    if narrative_text is None:
        raise HTTPException(
            status_code=503,
            detail="Narrative unavailable — ANTHROPIC_API_KEY not configured or API error",
        )

    return {"folio": folio, "narrative": narrative_text}
