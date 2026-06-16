"""
Claude AI buyer narrative generator.

Generates a concise, structured buyer briefing from structured report data.
The AI is strictly constrained to facts in the report — it cannot assert
anything not present in the data.

Model: claude-sonnet-4-6
"""
from __future__ import annotations

import json
import os
from typing import Optional

from app.models.schema import BuyerInsightsReport, PropertyReport


def _build_prompt(report: PropertyReport, insights: BuyerInsightsReport) -> str:
    prop = report.property
    env  = report.environmental

    flood_zone = "unknown"
    if env.flood and env.flood.fld_zone:
        sfha = "SFHA" if env.flood.sfha else "non-SFHA"
        flood_zone = f"{env.flood.fld_zone} ({sfha})"

    wetland = "No mapped wetland signal"
    if env.wetlands and env.wetlands.found:
        wetland = f"Mapped wetland signal: {env.wetlands.wetland_type or 'unknown type'}"

    water = "no record found"
    sewer = "no record found"
    if report.utilities:
        if report.utilities.water_service_area and report.utilities.water_service_area.found:
            water = "service area found"
        if report.utilities.sewer_service_area and report.utilities.sewer_service_area.found:
            sewer = "service area found"

    road = "no county road record"
    if report.road and report.road.found:
        road = f"{report.road.street or 'unnamed'} ({report.road.maintained_by or 'authority unknown'})"

    score_line = f"{insights.buildability.score}/100 ({insights.buildability.label})"

    top_findings_text = "\n".join(
        f"- [{f.risk_level}] {f.title}: {f.finding}" for f in insights.top_findings[:5]
    )

    data_block = json.dumps({
        "folio": prop.folio,
        "address": prop.address,
        "acreage_gross": prop.acreage_gross,
        "use": prop.use,
        "zoning": f"{report.zoning.nzone} — {report.zoning.nzone_desc}" if report.zoning else "unknown",
        "future_land_use": f"{report.future_land_use.flue} — {report.future_land_use.flu_desc}" if report.future_land_use else "unknown",
        "just_value": prop.just_value,
        "assessed_value": prop.assessed_value,
        "flood_zone": flood_zone,
        "wetland": wetland,
        "water": water,
        "sewer": sewer,
        "road": road,
        "buildability_score": score_line,
    }, indent=2)

    return f"""You are a property due diligence analyst writing a buyer briefing for a parcel in Hillsborough County, FL.

STRUCTURED DATA (only assert facts from this block):
{data_block}

TOP FINDINGS:
{top_findings_text}

Write a buyer briefing with these three sections:
1. **Property at a Glance** (2–3 sentences): address, acreage, current use, zoning/FLU
2. **Key Risks** (3–5 bullet points): only from the findings above, use exact safe wording for wetlands ("professional delineation required"), utilities ("active connection/cost not verified"), flood zones
3. **Recommended Next Steps** (3–5 bullets): actionable due diligence steps before making an offer

RULES:
- Only assert facts present in the structured data block above
- Do not say "no liens exist", "no violations", or "property is buildable"
- Use passive voice for uncertain items ("No records were found in the tested source")
- Keep total response under 350 words
- End with: "This is a screening-level briefing based on public records only. Engage licensed professionals before making a purchase decision."
"""


async def generate_narrative(
    report: PropertyReport,
    insights: BuyerInsightsReport,
) -> Optional[str]:
    """
    Call Claude API to generate the buyer briefing.
    Returns None if ANTHROPIC_API_KEY is not set or call fails.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _build_prompt(report, insights)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception:
        return None
