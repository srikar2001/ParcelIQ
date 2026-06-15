"""
Insight Engine.

Converts a PropertyReport into a BuyerInsightsReport (top findings +
buildability score) per 04_INSIGHT_ENGINE_RULES.md.

The UI does not think. This module thinks. Wording here should stay
close to the safe-wording guidance in 08_DISCLAIMERS_AND_CONFIDENCE.md —
never assert absence of risk, only absence of records in a tested source.
"""
from __future__ import annotations

from app.models.schema import (
    BuildabilityScore,
    Insight,
    PropertyReport,
)


def run_insight_engine(report: PropertyReport) -> tuple[list[Insight], BuildabilityScore]:
    insights: list[Insight] = []
    score = 100
    deductions: list[dict] = []
    additions: list[dict] = []

    env = report.environmental

    # ── Wetlands
    if env.wetlands and env.wetlands.found:
        insights.append(
            Insight(
                title="Mapped wetland signal found",
                category="environmental",
                risk_level="High",
                finding=(
                    f"NWI shows {env.wetlands.wetland_type or 'a wetland'} "
                    f"at sampled point."
                ),
                buyer_meaning=(
                    "Mapped wetland signal may affect buildable area, clearing, "
                    "septic, drainage, fill, and permitting."
                ),
                next_step="Request wetland delineation / EPC or environmental consultant review.",
                source_ids=["03"],
                confidence="screening",
            )
        )
        score -= 25
        deductions.append({"reason": "Wetland Found", "amount": -25})

    # ── Flood (Source 02)
    if env.flood:
        if env.flood.fld_zone == "X" and env.flood.sfha is False:
            insights.append(
                Insight(
                    title="FEMA flood zone lower at sampled point",
                    category="environmental",
                    risk_level="Low",
                    finding=f"FEMA Zone {env.flood.fld_zone}; SFHA_TF F.",
                    buyer_meaning=(
                        "Flood insurance hazard appears lower at sampled point, "
                        "but this does not clear wetlands/drainage/septic risk."
                    ),
                    next_step="Verify lender/insurer flood requirements.",
                    source_ids=["02"],
                    confidence="verified_api",
                )
            )
            score += 3
            additions.append({"reason": "FEMA Zone X / SFHA False", "amount": 3})
        elif env.flood.fld_zone and env.flood.fld_zone.startswith(("A", "V")):
            insights.append(
                Insight(
                    title="FEMA flood zone indicates elevated risk",
                    category="environmental",
                    risk_level="High",
                    finding=f"FEMA Zone {env.flood.fld_zone}.",
                    buyer_meaning="Property is in a Special Flood Hazard Area; flood insurance likely required.",
                    next_step="Verify flood insurance requirements and base flood elevation with lender/insurer.",
                    source_ids=["02"],
                    confidence="verified_api",
                )
            )

    # ── Utilities (07A/07B)
    if report.utilities:
        water = report.utilities.water_service_area
        sewer = report.utilities.sewer_service_area
        if (water and water.found) or (sewer and sewer.found):
            services = []
            if water and water.found:
                services.append("water")
            if sewer and sewer.found:
                services.append("sewer")
            insights.append(
                Insight(
                    title="Water and/or sewer service area found",
                    category="utilities",
                    risk_level="Medium",
                    finding=f"Hillsborough County {', '.join(services)} service area returned at sampled point.",
                    buyer_meaning="Positive signal, but does not guarantee active connection or low connection cost.",
                    next_step="Verify main/meter/tap/extension costs.",
                    source_ids=["07A", "07B"],
                    confidence="verified_api",
                )
            )
            score -= 5
            deductions.append({"reason": "Utilities Connection Unverified", "amount": -5})

    # ── Road access (08)
    if report.road and report.road.found:
        insights.append(
            Insight(
                title="County-maintained road found near parcel",
                category="access",
                risk_level="Medium",
                finding=f"Road record returned: {report.road.street}, maintained by {report.road.maintained_by}.",
                buyer_meaning="Road-level county maintenance signal only.",
                next_step="Verify legal ingress/egress, frontage, driveway permit, and easements/title.",
                source_ids=["08"],
                confidence="partial",
            )
        )

    # Legal access not verified — applies regardless of road match,
    # since road record alone doesn't verify parcel-specific access.
    score -= 10
    deductions.append({"reason": "Legal Access Unverified", "amount": -10})

    # ── Permits / CO (09/10)
    if report.permits:
        if not report.permits.issued_permits_match_found and not report.permits.certificate_of_occupancy_match_found:
            insights.append(
                Insight(
                    title="No confirmed permit or CO records found",
                    category="permits",
                    risk_level="Unknown",
                    finding="No confirmed records found in tested source.",
                    buyer_meaning="This does not mean no permits exist — county GIS address matching is unreliable.",
                    next_step="Search county permit portal directly by parcel/folio for full history.",
                    source_ids=["09", "10"],
                    confidence="screening",
                )
            )
            score -= 3
            deductions.append({"reason": "No Permit Match", "amount": -3})

    # ── Address confirmed
    score += 2
    additions.append({"reason": "Address Confirmed", "amount": 2})

    # Clamp score
    score = max(0, min(100, score))

    if score >= 85:
        label = "Excellent"
    elif score >= 70:
        label = "Good"
    elif score >= 55:
        label = "Moderate"
    elif score >= 40:
        label = "Caution"
    else:
        label = "High Risk"

    buildability = BuildabilityScore(
        score=score,
        label=label,
        deductions=deductions,
        additions=additions,
    )

    return insights, buildability
