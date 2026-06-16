"""
Insight Engine v2 — Cross-source pattern detection + buildability scoring.

Safe wording rules (never violate):
  - "No records found in tested source" (never "no liens exist")
  - "Mapped wetland signal found; professional delineation required"
  - "Service area found; active connection/cost not verified"
  - "This is a screening-level score based on public records only"
"""
from __future__ import annotations

from app.models.schema import BuildabilityScore, Insight, PropertyReport


# ── Scoring constants
_D_WETLAND          = -30
_D_SFHA             = -15
_D_WETLAND_SEPTIC   = -12  # Pattern 1
_D_UTIL_GAP_WET     = -8   # Pattern 2
_D_NO_UTILITY       = -5
_D_NO_ROAD          = -10  # Pattern 7
_D_NO_PERMITS       = -3
_D_VALUE_GAP        = -2   # Pattern 3

_A_ZONE_X           = +5
_A_BOTH_UTILITIES   = +4
_A_ONE_UTILITY      = +2
_A_ROAD_FOUND       = +2
_A_ADDR_CONFIRMED   = +3


def run_insight_engine(report: PropertyReport) -> tuple[list[Insight], BuildabilityScore]:
    insights: list[Insight] = []
    score = 100
    deductions: list[dict] = []
    additions: list[dict] = []

    env  = report.environmental
    prop = report.property

    # ── 1. Wetlands (Source 03)
    wetland_found = bool(env.wetlands and env.wetlands.found)
    if wetland_found:
        wt = env.wetlands
        insights.append(Insight(
            title="Mapped wetland signal found",
            category="environmental",
            risk_level="High",
            finding=(
                f"NWI polygon intersects parcel: {wt.wetland_type or 'wetland features'} "
                f"({wt.attribute or 'type unspecified'}). "
                "Polygon acreage is the NWI polygon size, not parcel acreage."
            ),
            buyer_meaning=(
                "Mapped wetland signal found; professional delineation required. "
                "Buildable area, septic drainfield placement, clearing, fill, and "
                "drainage permits may all be affected. Gross acreage is not usable acreage."
            ),
            next_step=(
                "Ask seller for any existing wetland delineation study. "
                "If none exists, engage a licensed environmental consultant before offering. "
                "This is the critical unknown for pricing this parcel."
            ),
            source_ids=["03"],
            confidence="screening",
        ))
        score += _D_WETLAND
        deductions.append({
            "reason": "Mapped wetland signal — NWI polygon intersects parcel",
            "amount": _D_WETLAND,
            "source": "03",
        })

    # ── 2. Flood Zone (Source 02)
    flood_sfha   = bool(env.flood and env.flood.sfha is True)
    flood_zone_x = bool(env.flood and env.flood.fld_zone == "X" and not env.flood.sfha)

    if flood_sfha:
        zone = env.flood.fld_zone
        insights.append(Insight(
            title=f"FEMA flood zone {zone} — Special Flood Hazard Area",
            category="environmental",
            risk_level="High",
            finding=f"FEMA Zone {zone} (SFHA: True). Flood insurance typically required by lenders.",
            buyer_meaning=(
                "Property is in a Special Flood Hazard Area. Flood insurance will likely "
                "be required and is priced by FEMA actuarial tables. Elevation certificate "
                "may be required. Construction standards are elevated."
            ),
            next_step=(
                "Get a flood insurance quote BEFORE making an offer. "
                "Request elevation certificate from seller. "
                "Verify Base Flood Elevation at msc.fema.gov."
            ),
            source_ids=["02"],
            confidence="verified_api",
        ))
        score += _D_SFHA
        deductions.append({
            "reason": f"SFHA flood zone {zone}",
            "amount": _D_SFHA,
            "source": "02",
        })
    elif flood_zone_x:
        insights.append(Insight(
            title="FEMA Zone X — minimal flood hazard at sampled area",
            category="environmental",
            risk_level="Low",
            finding="FEMA Zone X; SFHA: False. Minimal flood hazard at parcel area.",
            buyer_meaning=(
                "Zone X is a positive signal at the sampled parcel area, but does not "
                "clear wetland, drainage, or septic risks. Does not certify every point "
                "on large or irregular parcels."
            ),
            next_step="Verify lender flood insurance requirements. Zone X can border SFHA zones on adjacent parcels.",
            source_ids=["02"],
            confidence="verified_api",
        ))
        score += _A_ZONE_X
        additions.append({
            "reason": "FEMA Zone X / SFHA False",
            "amount": _A_ZONE_X,
            "source": "02",
        })

    # ── 3. Utilities (Sources 07A/07B)
    water_found = bool(
        report.utilities
        and report.utilities.water_service_area
        and report.utilities.water_service_area.found
    )
    sewer_found = bool(
        report.utilities
        and report.utilities.sewer_service_area
        and report.utilities.sewer_service_area.found
    )

    if water_found or sewer_found:
        services = []
        if water_found: services.append("water")
        if sewer_found: services.append("sewer")
        svc_str = " and ".join(s.capitalize() for s in services)
        insights.append(Insight(
            title=f"{svc_str} service area found",
            category="utilities",
            risk_level="Low",
            finding=(
                f"Hillsborough County {', '.join(services)} service area found at parcel. "
                "Service area found; active connection/cost not verified."
            ),
            buyer_meaning=(
                "Service area presence is a positive signal but does not guarantee an active "
                "connection, meter, main extension, or affordable connection cost. "
                "Tap fees and main extension costs vary significantly."
            ),
            next_step=(
                "Confirm active connection, tap fee, meter status, and any required main "
                "extension with the county utility office before offer."
            ),
            source_ids=["07A", "07B"],
            confidence="verified_api",
        ))
        if water_found and sewer_found:
            score += _A_BOTH_UTILITIES
            additions.append({"reason": "Both water and sewer service areas found", "amount": _A_BOTH_UTILITIES, "source": "07A+07B"})
        else:
            score += _A_ONE_UTILITY
            additions.append({"reason": f"{', '.join(services)} service area found (partial)", "amount": _A_ONE_UTILITY, "source": "07A+07B"})
    else:
        insights.append(Insight(
            title="No public utility service area found",
            category="utilities",
            risk_level="Medium",
            finding="No water or sewer service area record returned at sampled point.",
            buyer_meaning=(
                "No public utility service area returned. Private well and/or septic likely required. "
                "Well and septic have upfront costs ($8,000–$22,000+), permitting requirements, "
                "and are not guaranteed on all soil types."
            ),
            next_step=(
                "Call Hillsborough County DOH (813) 307-8059 with the folio number to verify "
                "septic feasibility. Get well and septic cost estimates before making an offer."
            ),
            source_ids=["07A", "07B"],
            confidence="verified_api",
        ))
        score += _D_NO_UTILITY
        deductions.append({"reason": "No utility service area at sampled point", "amount": _D_NO_UTILITY, "source": "07A+07B"})

    # ── PATTERN 1: Wetland + Septic Stack (worst-case combination)
    if wetland_found and not water_found and not sewer_found:
        insights.insert(0, Insight(
            title="Wetland + no utilities: compounded development risk",
            category="environmental",
            risk_level="High",
            finding=(
                "Mapped wetland signal detected AND no public utility service area at parcel. "
                "Private septic is likely required, but wetland presence creates drainfield placement uncertainty."
            ),
            buyer_meaning=(
                "This is the highest-risk combination for any build plan. Wetlands may conflict "
                "with septic drainfield setback requirements. Buildable area may be severely constrained. "
                "An engineered septic system may be required at significantly higher cost."
            ),
            next_step=(
                "Do not make an offer without: (1) a professional wetland delineation confirming "
                "buildable area, and (2) a pre-application meeting with Hillsborough DOH on septic "
                "feasibility. These two steps define what you are actually buying."
            ),
            source_ids=["03", "07A", "07B"],
            confidence="screening",
        ))
        score += _D_WETLAND_SEPTIC
        deductions.append({"reason": "PATTERN 1: Wetland + no-utility compounded risk", "amount": _D_WETLAND_SEPTIC, "source": "03+07A+07B"})

    # ── PATTERN 2: Utility Gap (partial utilities + wetland)
    elif wetland_found and (water_found != sewer_found):
        score += _D_UTIL_GAP_WET
        deductions.append({"reason": "PATTERN 2: Partial utilities + wetland detected", "amount": _D_UTIL_GAP_WET, "source": "03+07A+07B"})

    # ── 4. Road access (Source 08)
    if report.road and report.road.found:
        insights.append(Insight(
            title="County road record found",
            category="access",
            risk_level="Low",
            finding=(
                f"Road record: {report.road.street or '—'}, "
                f"maintained by {report.road.maintained_by or '—'}, "
                f"authority: {report.road.authority or '—'}."
            ),
            buyer_meaning=(
                "County road maintenance signal found. Road-level data only — does NOT verify "
                "legal ingress/egress, parcel frontage, driveway permit, or easements."
            ),
            next_step="Verify legal access, frontage, and driveway permit rights through title search.",
            source_ids=["08"],
            confidence="partial",
        ))
        score += _A_ROAD_FOUND
        additions.append({"reason": "County road record found", "amount": _A_ROAD_FOUND, "source": "08"})
    else:
        # PATTERN 7: Possible landlocked parcel
        insights.append(Insight(
            title="No county road record found — access risk",
            category="access",
            risk_level="High",
            finding="No county road record returned for this parcel address. Screening only — does not confirm landlocked status.",
            buyer_meaning=(
                "No county road record found at this address. Parcel may lack confirmed legal access. "
                "A parcel without confirmed legal access cannot be financed or permitted."
            ),
            next_step=(
                "Verify access, easements, and road frontage in title search BEFORE making any offer. "
                "This is a dealbreaker risk if unresolved."
            ),
            source_ids=["08"],
            confidence="not_validated",
        ))
        score += _D_NO_ROAD
        deductions.append({"reason": "PATTERN 7: No road record — access risk", "amount": _D_NO_ROAD, "source": "08"})

    # ── 5. Permits / CO (Sources 09/10)
    if report.permits:
        if not report.permits.issued_permits_match_found and not report.permits.certificate_of_occupancy_match_found:
            insights.append(Insight(
                title="No confirmed permit or CO records found in tested source",
                category="permits",
                risk_level="Unknown",
                finding="No confirmed records found in tested source.",
                buyer_meaning=(
                    "No match found in county GIS permit layer. This does not mean no permits exist — "
                    "GIS address matching is unreliable. Search directly by folio for complete history."
                ),
                next_step="Search HillsGovHub permit portal directly by folio/parcel number for complete permit history.",
                source_ids=["09", "10"],
                confidence="screening",
            ))
            score += _D_NO_PERMITS
            deductions.append({"reason": "No confirmed permit match in tested source", "amount": _D_NO_PERMITS, "source": "09+10"})

    # ── PATTERN 3: Value/Assessment divergence
    if prop.just_value and prop.assessed_value and prop.just_value > 0:
        divergence = abs(prop.just_value - prop.assessed_value) / prop.just_value
        if divergence > 0.15:
            insights.append(Insight(
                title=f"Assessed value diverges {round(divergence * 100)}% from market value",
                category="tax",
                risk_level="Medium",
                finding=(
                    f"Just (market) value ${prop.just_value:,.0f} vs assessed ${prop.assessed_value:,.0f} — "
                    f"{round(divergence * 100)}% gap."
                ),
                buyer_meaning=(
                    "Significant divergence between market and assessed value may indicate homestead, "
                    "agricultural, or other exemption. If the exemption is lost at sale, tax burden "
                    "may increase substantially."
                ),
                next_step=(
                    "Verify with Hillsborough Tax Collector: does this parcel carry homestead, "
                    "Greenbelt, or other exemption that will be removed at sale?"
                ),
                source_ids=["01"],
                confidence="verified_api",
            ))
            score += _D_VALUE_GAP
            deductions.append({"reason": "PATTERN 3: Value/assessment gap > 15%", "amount": _D_VALUE_GAP, "source": "01"})

    # ── 6. Address point confirmed (Source 12)
    addr_confirmed = any(
        e.source_id == "12" and e.status == "verified_api"
        for e in report.source_evidence
    )
    if addr_confirmed:
        score += _A_ADDR_CONFIRMED
        additions.append({"reason": "Official address confirmed in address point layer", "amount": _A_ADDR_CONFIRMED, "source": "12"})

    # ── Clamp and label
    score = max(0, min(100, score))

    if   score >= 90: label = "Excellent"
    elif score >= 75: label = "Good"
    elif score >= 55: label = "Moderate"
    elif score >= 35: label = "Caution"
    else:             label = "High Risk"

    buildability = BuildabilityScore(
        score=score,
        label=label,
        deductions=deductions,
        additions=additions,
    )

    return insights, buildability
