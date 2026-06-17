"""
AI buyer narrative generator.

Uses Google Gemini (gemini-1.5-flash) as the primary model.
Falls back to Anthropic Claude (claude-sonnet-4-6) if ANTHROPIC_API_KEY is set.
Returns None if neither key is configured.

Safe wording rules (enforced via prompt):
  - Never say property is safe or recommended
  - Never assert facts not in the structured report data
  - Plain English, first-time buyer level
"""
from __future__ import annotations

import json
from typing import Optional

from app.core.config import settings
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

    score = insights.buildability.score
    label = insights.buildability.label

    findings_text = "\n".join(
        f"- [{f.risk_level}] {f.title}: {f.buyer_meaning}"
        for f in insights.top_findings[:5]
    )

    just_val = f"{prop.just_value:,.0f}" if prop.just_value else "unknown"
    assessed_val = f"{prop.assessed_value:,.0f}" if prop.assessed_value else "unknown"

    return f"""You are a property due diligence expert writing a plain-English buyer briefing.

PROPERTY DATA (only assert facts from this block):
Address: {prop.address}, Hillsborough County FL
Owner: {prop.owner or 'unknown'}
Acreage: {prop.acreage_gross} acres (gross — usable area unconfirmed)
Use classification: {prop.use or 'unspecified'}
Zoning: {report.zoning.nzone + ' — ' + report.zoning.nzone_desc if report.zoning else 'unknown'}
Future land use: {report.future_land_use.flue + ' — ' + (report.future_land_use.flu_desc or '') if report.future_land_use else 'unknown'}
Just (market) value: ${just_val}
Assessed value: ${assessed_val}
Flood zone: {flood_zone}
Wetland screening: {wetland}
Water service area: {water}
Sewer service area: {sewer}
Road access: {road}
Buildability Score: {score}/100 — {label}

TOP FINDINGS FROM AUTOMATED SCREENING:
{findings_text}

Write a buyer briefing with EXACTLY these 5 sections:
1. SUMMARY (2-3 sentences: what kind of property, where, key concern)
2. TOP RISKS (explain the 3 biggest concerns simply — what it means for a buyer, no jargon)
3. POSITIVE SIGNALS (what looks good about this property from the data)
4. WHAT TO DO NEXT (numbered checklist of 4-5 steps before making an offer)
5. IMPORTANT (one sentence: this is screening only, verify with licensed professionals)

RULES — never violate:
- Never say the property is safe, recommended, or buildable
- Never claim facts not in the data block above
- Never say "no liens exist" — only "no records found in tested source"
- Plain English only — write for a first-time buyer
- Be honest about what is unknown
- Under 400 words total
- This is a screening-level briefing based on public records only"""


async def generate_narrative(
    report: PropertyReport,
    insights: BuyerInsightsReport,
) -> Optional[str]:
    prompt = _build_prompt(report, insights)

    # Try Gemini first
    google_key = settings.google_api_key
    if google_key and google_key != "paste_your_NEW_gemini_key_here":
        try:
            import google.generativeai as genai
            genai.configure(api_key=google_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            text = response.text.strip()
            if text:
                return text
        except Exception as exc:
            print(f"[Narrative] Gemini error: {exc}")

    # Fallback: Anthropic Claude
    anthropic_key = settings.anthropic_api_key
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text.strip()
        except Exception as exc:
            print(f"[Narrative] Anthropic error: {exc}")

    return None
