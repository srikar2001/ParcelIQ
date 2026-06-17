"""
GET /api/narrative/{folio}

Returns 503 immediately if no AI key is configured.
Checks Supabase cache for an existing narrative first.
If no cached narrative but a cached report_json exists, uses that to
reconstruct the PropertyReport instead of re-running all 13 GIS sources.
Only falls back to the full pipeline if there's no cached report at all.
"""
from __future__ import annotations

import asyncio

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
from app.core.config import settings
from app.core.database import cache_report, get_cached_report
from app.core.insight_engine import run_insight_engine
from app.core.narrative import generate_narrative
from app.models.schema import (
    BuyerInsightsReport,
    EnvironmentalSection,
    PropertyReport,
    UtilitiesSection,
)
from app.routers.report import _load_manual_sources

router = APIRouter(prefix="/api", tags=["narrative"])


def _has_ai_key() -> bool:
    g = settings.google_api_key
    a = settings.anthropic_api_key
    return bool(a) or (bool(g) and g != "paste_your_NEW_gemini_key_here")


@router.get("/narrative/{folio}")
async def get_narrative(folio: str):
    # Fast-fail if no AI key is configured — no point running the pipeline
    if not _has_ai_key():
        raise HTTPException(
            status_code=503,
            detail="Narrative unavailable — set GOOGLE_API_KEY or ANTHROPIC_API_KEY in .env",
        )

    # Check Supabase cache for an existing narrative
    cached = await get_cached_report(folio)
    if cached and cached.get("narrative"):
        return {"folio": folio, "narrative": cached["narrative"], "cached": True}

    # Build the report object needed by generate_narrative.
    # Prefer rebuilding from the cached report_json to avoid re-hitting all 13 APIs.
    report, buyer_insights = await _build_report(folio, cached)

    narrative_text = await generate_narrative(report, buyer_insights)

    if narrative_text is None:
        raise HTTPException(
            status_code=503,
            detail="Narrative unavailable — AI model returned no content (check API key validity)",
        )

    # Persist narrative into the existing cache row
    try:
        await cache_report(
            folio=folio,
            address=report.property.address or "",
            report_json={},
            narrative=narrative_text,
            score=buyer_insights.buildability.score,
        )
    except Exception:
        pass

    return {"folio": folio, "narrative": narrative_text, "cached": False}


async def _build_report(
    folio: str,
    cached: dict | None,
) -> tuple[PropertyReport, BuyerInsightsReport]:
    """
    Build PropertyReport + BuyerInsightsReport.
    Uses cached report_json when available to skip re-calling GIS APIs.
    Falls back to full pipeline only if no cached data exists.
    """
    cached_rj = cached.get("report_json") if cached else None
    pr_block = (cached_rj or {}).get("property_report", {})

    if pr_block:
        # ── Fast path: reconstruct from cached JSON
        prop_block = pr_block.get("property", {})
        env_block  = pr_block.get("environmental", {})

        from app.models.schema import (
            PropertySummary, FloodInfo, WetlandInfo, EvacZoneInfo,
            ZoningInfo, FutureLandUseInfo, ServiceAreaInfo, RoadInfo,
        )

        prop = PropertySummary(
            folio=prop_block.get("folio", folio),
            address=prop_block.get("address"),
            owner=prop_block.get("owner"),
            acreage_gross=prop_block.get("acreage_gross"),
            just_value=prop_block.get("just_value"),
            assessed_value=prop_block.get("assessed_value"),
            use=prop_block.get("use"),
            centroid=prop_block.get("centroid"),
        )

        def _flood(d: dict) -> FloodInfo | None:
            if not d:
                return None
            return FloodInfo(fld_zone=d.get("fld_zone"), sfha=d.get("sfha"))

        def _wetland(d: dict) -> WetlandInfo | None:
            if not d:
                return None
            return WetlandInfo(found=d.get("found", False), wetland_type=d.get("wetland_type"), attribute=d.get("attribute"))

        def _evac(d: dict) -> EvacZoneInfo | None:
            if not d:
                return None
            return EvacZoneInfo(found=d.get("found", False), zone=d.get("zone"))

        def _zoning(d: dict) -> ZoningInfo | None:
            if not d:
                return None
            return ZoningInfo(nzone=d.get("nzone"), nzone_desc=d.get("nzone_desc"))

        def _flu(d: dict) -> FutureLandUseInfo | None:
            if not d:
                return None
            return FutureLandUseInfo(flue=d.get("flue"), flu_desc=d.get("flu_desc"))

        def _svc(d: dict) -> ServiceAreaInfo | None:
            if not d:
                return None
            return ServiceAreaInfo(found=d.get("found", False), service_by=d.get("service_by"))

        def _road(d: dict) -> RoadInfo | None:
            if not d:
                return None
            return RoadInfo(found=d.get("found", False), street=d.get("street"), maintained_by=d.get("maintained_by"))

        util_block = pr_block.get("utilities", {}) or {}
        report = PropertyReport(
            property=prop,
            environmental=EnvironmentalSection(
                flood=_flood(env_block.get("flood") or {}),
                wetlands=_wetland(env_block.get("wetlands") or {}),
                evacuation_zone=_evac(env_block.get("evacuation_zone") or {}),
            ),
            zoning=_zoning(pr_block.get("zoning") or {}),
            future_land_use=_flu(pr_block.get("future_land_use") or {}),
            utilities=UtilitiesSection(
                water_service_area=_svc(util_block.get("water_service_area") or {}),
                sewer_service_area=_svc(util_block.get("sewer_service_area") or {}),
            ),
            road=_road(pr_block.get("road") or {}),
            manual_sources=_load_manual_sources(),
            source_evidence=[],
        )
    else:
        # ── Slow path: full pipeline (no cached data available)
        summary, attr_evidence = await parcel.get_parcel_attributes(folio)
        if summary is None:
            raise HTTPException(status_code=404, detail=f"No parcel found for FOLIO {folio}")

        geometry, centroid, geom_evidence = await parcel.get_parcel_geometry(folio)
        summary.geometry = geometry
        summary.centroid = centroid

        if centroid is None:
            raise HTTPException(status_code=422, detail="No geometry for this parcel")

        lat, lon = centroid["lat"], centroid["lon"]
        source_evidence = [e for e in (attr_evidence, geom_evidence) if e]

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

        def _unpack(r):
            return (None, None) if isinstance(r, Exception) else r

        flood_info,   flood_ev   = _unpack(results[0])
        wetland_info, wetland_ev = _unpack(results[1])
        evac_info,    evac_ev    = _unpack(results[2])
        zoning_info,  zoning_ev  = _unpack(results[3])
        flu_info,     flu_ev     = _unpack(results[4])
        water_info,   water_ev   = _unpack(results[5])
        sewer_info,   sewer_ev   = _unpack(results[6])
        road_info,    road_ev    = _unpack(results[7])

        if isinstance(results[8], Exception):
            permits_info, permits_evs = None, []
        else:
            permits_info, permits_evs = results[8]

        for ev in (flood_ev, wetland_ev, evac_ev, zoning_ev, flu_ev,
                   water_ev, sewer_ev, road_ev, *permits_evs):
            if ev:
                source_evidence.append(ev)

        report = PropertyReport(
            property=summary,
            environmental=EnvironmentalSection(
                flood=flood_info, wetlands=wetland_info, evacuation_zone=evac_info,
            ),
            zoning=zoning_info,
            future_land_use=flu_info,
            utilities=UtilitiesSection(
                water_service_area=water_info, sewer_service_area=sewer_info,
            ),
            road=road_info,
            permits=permits_info,
            manual_sources=_load_manual_sources(),
            source_evidence=source_evidence,
        )

    insights, buildability = run_insight_engine(report)
    buyer_insights = BuyerInsightsReport(
        folio=folio, buildability=buildability, top_findings=insights,
    )
    return report, buyer_insights
