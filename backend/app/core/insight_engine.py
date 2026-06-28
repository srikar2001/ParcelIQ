from __future__ import annotations

from app.models.schema import BuildabilityScore, Insight, PropertyReport

SFHA_ZONES = {"AE", "VE", "V", "AO", "AH"}
AGRI_CODES = {"50", "51", "52", "53", "54", "55", "56", "57", "58", "59"}
PUBLIC_CODES = {"86", "87", "88", "89", "99"}


def score_parcel(
    flood: dict,
    wetlands: dict,
    habitat: dict,
    epa: dict,
    roads: dict,
    parcel: dict,
    waterways: dict | None = None,
    elevation: dict | None = None,
    soil: dict | None = None,
    evac: dict | None = None,
    powerlines: dict | None = None,
    easement: dict | None = None,
) -> dict:
    flags: list[str] = []
    positives: list[str] = []

    sources_checked = [
        flood.get("source", "FEMA NFHL"),
        wetlands.get("source", "USFWS NWI"),
        habitat.get("source", "USFWS Critical Habitat"),
        epa.get("source", "EPA ECHO Superfund"),
        roads.get("source", "OpenStreetMap"),
        parcel.get("source", "FL DOR Cadastral"),
    ]
    if waterways:
        sources_checked.append(waterways.get("source", "OpenStreetMap"))
    if elevation:
        sources_checked.append(elevation.get("source", "USGS National Elevation Dataset"))
    if soil:
        sources_checked.append(soil.get("source", "USDA Web Soil Survey"))
    if evac:
        sources_checked.append(evac.get("source", "FL Division of Emergency Management"))
    if powerlines:
        sources_checked.append(powerlines.get("source", "OpenStreetMap"))
    if easement:
        sources_checked.append(easement.get("source", "USDA NRCS"))

    # ── STEP 1: Auto-kill checks (mark fatal flaws, continue scoring)
    auto_kill = False
    auto_kill_reason: str | None = None

    flood_zone = (flood.get("zone") or "").strip().upper()

    # Flag when FEMA API itself failed (distinct from area not being mapped)
    if flood_zone == "ERROR":
        flags.append("Flood zone data unavailable — verify with FEMA manually")

    if flood_zone in SFHA_ZONES and flood.get("sfha") is True:
        reason = f"FEMA Flood Zone {flood_zone}"
        flags.append(reason)
        if not auto_kill:
            auto_kill_reason = reason
        auto_kill = True

    # Flag when NWI API itself failed
    if wetlands.get("error") is True:
        flags.append("Wetland data unavailable — verify with USFWS NWI manually")

    if wetlands.get("wetland_on_parcel") is True:
        reason = "Wetlands on parcel (USFWS NWI)"
        flags.append(reason)
        if not auto_kill:
            auto_kill_reason = reason
        auto_kill = True

    land_use = str(parcel.get("land_use_code") or "")
    if land_use in PUBLIC_CODES:
        reason = "Public/conservation land"
        flags.append(reason)
        if not auto_kill:
            auto_kill_reason = reason
        auto_kill = True

    if habitat.get("habitat_found") is True:
        species = habitat.get("species") or []
        sp = ", ".join(species[:2]) if species else "listed species"
        reason = f"Critical habitat: {sp}"
        flags.append(reason)
        if not auto_kill:
            auto_kill_reason = reason
        auto_kill = True

    if easement and easement.get("easement_found") is True:
        etype = easement.get("easement_type") or "conservation easement"
        reason = f"Conservation easement: {etype} — development permanently restricted"
        flags.append(reason)
        if not auto_kill:
            auto_kill_reason = reason
        auto_kill = True

    # ── STEP 2: Scoring (always runs — auto-kills get a -60 penalty per fatal flaw)
    score = 100
    if auto_kill:
        score -= 60

    acreage = parcel.get("acreage")
    just_value = parcel.get("just_value")
    land_value = parcel.get("land_value")
    last_sale_price = parcel.get("last_sale_price")
    building_count = parcel.get("building_count") or 0

    # Deductions
    if epa.get("contamination_found"):
        score -= 25
        flags.append("EPA contamination record found")

    # FIX 4 — OSM gaps in rural FL are common, not the same as landlocked
    if roads.get("road_surface") == "none":
        score -= 5
        flags.append("Road data unavailable — verify access")

    if acreage is not None and acreage < 0.25:
        score -= 20
        flags.append(f"Parcel very small ({round(acreage, 3)} ac)")

    if flood_zone == "A":
        score -= 15
        flags.append("FEMA Zone A (undetermined flood risk)")

    if land_use in AGRI_CODES:
        score -= 15
        flags.append(f"Agricultural land use code ({land_use})")

    # FIX 2 — Data gap is not the same as a bad parcel
    if not parcel.get("found"):
        score -= 5
        flags.append("County data unavailable")

    # FIX 3 — Nearby wetlands penalty reduced
    if wetlands.get("wetland_nearby") is True:
        score -= 8
        flags.append("Wetlands nearby (within ~100m)")

    # FIX 4 — Updated flag text for unknown surface
    if roads.get("road_surface") == "unknown":
        score -= 8
        flags.append("Road type unconfirmed")

    if roads.get("road_surface") == "dirt":
        score -= 8
        flags.append("Dirt/unpaved road access")

    # FIX 5 — Reduce noise flag weights
    if building_count == 0 and last_sale_price is None:
        score -= 2
        flags.append("Vacant lot with no sale history")

    if last_sale_price is not None and last_sale_price < 1000:
        score -= 2
        flags.append(f"Last sale price very low (${last_sale_price:,.0f})")

    # Waterway proximity (Task 31)
    if waterways and waterways.get("waterway_nearby"):
        wtype = waterways.get("waterway_type", "waterway")
        score -= 5
        flags.append(f"Canal/waterway nearby ({wtype}) — check easements and drainage")

    if just_value and land_value and just_value > 0:
        if abs(just_value - land_value) / max(just_value, 1) > 0.15:
            score -= 2
            flags.append("Just value and land value diverge >15%")

    # Elevation (USGS)
    if elevation:
        elev_ft = elevation.get("elevation_ft")
        if elev_ft is not None:
            if elev_ft < 2:
                score -= 20
                flags.append(f"Very low elevation ({elev_ft} ft) — extreme storm surge / flood risk")
            elif elev_ft < 5:
                score -= 12
                flags.append(f"Low elevation ({elev_ft} ft) — storm surge and flood risk")
            elif elev_ft >= 20:
                score += 4
                positives.append(f"Good elevation ({elev_ft} ft) — above typical flood levels")

    # Soil drainage (USDA)
    if soil:
        septic = soil.get("septic_suitable")
        drainage = soil.get("soil_drainage") or ""
        if septic is False:
            score -= 12
            flags.append(f"Poor soil drainage ({drainage}) — septic system likely not feasible")
        elif septic is True:
            score += 4
            positives.append(f"Well-drained soil ({drainage}) — septic feasible")

    # Hurricane evacuation zone (FL FDEM)
    if evac:
        zone = evac.get("evac_zone")
        risk = evac.get("evac_risk")
        county = evac.get("evac_county", "")
        county_str = f" ({county} County)" if county else ""
        if zone == "A":
            score -= 18
            flags.append(f"Hurricane Evacuation Zone A{county_str} — highest surge risk, mandatory evac")
        elif zone == "B":
            score -= 12
            flags.append(f"Hurricane Evacuation Zone B{county_str} — high surge risk")
        elif zone == "C":
            score -= 6
            flags.append(f"Hurricane Evacuation Zone C{county_str} — moderate surge risk")
        elif zone in ("D", "E", "F"):
            score -= 2
            flags.append(f"Hurricane Evacuation Zone {zone}{county_str}")
        elif zone is None:
            positives.append("Not in a hurricane evacuation zone")

    # Power lines
    if powerlines:
        if powerlines.get("powerline_nearby") is False:
            score -= 5
            flags.append("No power lines within 1 mile — electric connection costly")

    # Additions
    if flood_zone == "X":
        score += 5
        positives.append("FEMA Zone X — minimal flood hazard")

    if not wetlands.get("wetland_on_parcel") and not wetlands.get("wetland_nearby"):
        score += 5
        positives.append("No wetlands on parcel or nearby")

    if roads.get("road_surface") == "paved":
        score += 3
        positives.append("Paved road access")

    if acreage is not None and 0.5 <= acreage <= 5:
        score += 3
        positives.append(f"Ideal parcel size ({round(acreage, 2)} ac)")

    if last_sale_price is not None and last_sale_price >= 10000:
        score += 3
        positives.append(f"Recent market sale (${last_sale_price:,.0f})")

    if building_count == 0:
        score += 2
        positives.append("Vacant/unimproved lot")

    if powerlines and powerlines.get("powerline_nearby") is True:
        dist = powerlines.get("powerline_distance", "nearby")
        score += 3 if dist == "< 500m" else 1
        positives.append(f"Power infrastructure {dist}")

    # ── STEP 3: Clamp + verdict (KILL or PURSUE only — no REVIEW tier)
    score = max(0, min(100, score))

    if auto_kill or score <= 34:
        verdict = "KILL"
    else:
        verdict = "PURSUE"

    kill_flags   = [f for f in flags if any(x in f.lower() for x in
                    ["wetland", "flood", "habitat", "public", "contamination", "easement"])]
    info_flags   = [f for f in flags if f not in kill_flags]

    return {
        "verdict": verdict,
        "score": score,
        "auto_kill": auto_kill,
        "auto_kill_reason": auto_kill_reason,
        "flags": flags,
        "positives": positives,
        "sources_checked": sources_checked,
        "kill_flags": kill_flags,
        "review_flags": [],
        "info_flags": info_flags,
    }


# ── Legacy engine for report.py / narrative.py (Hillsborough single-parcel flow)

_D_WETLAND          = -30
_D_SFHA             = -15
_D_WETLAND_SEPTIC   = -12
_D_UTIL_GAP_WET     = -8
_D_NO_UTILITY       = -5
_D_NO_ROAD          = -10
_D_NO_PERMITS       = -3
_D_VALUE_GAP        = -2
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

    wetland_found = bool(env.wetlands and env.wetlands.found)
    if wetland_found:
        wt = env.wetlands
        insights.append(Insight(
            title="Mapped wetland signal found",
            category="environmental",
            risk_level="High",
            finding=(
                f"NWI polygon intersects parcel: {wt.wetland_type or 'wetland features'} "
                f"({wt.attribute or 'type unspecified'})."
            ),
            buyer_meaning=(
                "Mapped wetland signal found; professional delineation required. "
                "Buildable area, septic drainfield placement, clearing, fill, and "
                "drainage permits may all be affected."
            ),
            next_step=(
                "Ask seller for any existing wetland delineation study. "
                "If none exists, engage a licensed environmental consultant before offering."
            ),
            source_ids=["03"],
            confidence="screening",
        ))
        score += _D_WETLAND
        deductions.append({"reason": "Mapped wetland signal — NWI polygon intersects parcel", "amount": _D_WETLAND, "source": "03"})

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
                "be required and is priced by FEMA actuarial tables."
            ),
            next_step="Get a flood insurance quote BEFORE making an offer. Request elevation certificate from seller.",
            source_ids=["02"],
            confidence="verified_api",
        ))
        score += _D_SFHA
        deductions.append({"reason": f"SFHA flood zone {zone}", "amount": _D_SFHA, "source": "02"})
    elif flood_zone_x:
        insights.append(Insight(
            title="FEMA Zone X — minimal flood hazard at sampled area",
            category="environmental",
            risk_level="Low",
            finding="FEMA Zone X; SFHA: False. Minimal flood hazard at parcel area.",
            buyer_meaning="Zone X is a positive signal but does not clear wetland, drainage, or septic risks.",
            next_step="Verify lender flood insurance requirements.",
            source_ids=["02"],
            confidence="verified_api",
        ))
        score += _A_ZONE_X
        additions.append({"reason": "FEMA Zone X / SFHA False", "amount": _A_ZONE_X, "source": "02"})

    water_found = bool(report.utilities and report.utilities.water_service_area and report.utilities.water_service_area.found)
    sewer_found = bool(report.utilities and report.utilities.sewer_service_area and report.utilities.sewer_service_area.found)

    if water_found or sewer_found:
        services = []
        if water_found: services.append("water")
        if sewer_found: services.append("sewer")
        svc_str = " and ".join(s.capitalize() for s in services)
        insights.append(Insight(
            title=f"{svc_str} service area found",
            category="utilities",
            risk_level="Low",
            finding=f"Hillsborough County {', '.join(services)} service area found at parcel. Service area found; active connection/cost not verified.",
            buyer_meaning="Service area presence is a positive signal but does not guarantee an active connection or affordable connection cost.",
            next_step="Confirm active connection, tap fee, meter status with the county utility office before offer.",
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
            buyer_meaning="No public utility service area returned. Private well and/or septic likely required.",
            next_step="Call Hillsborough County DOH with the folio number to verify septic feasibility.",
            source_ids=["07A", "07B"],
            confidence="verified_api",
        ))
        score += _D_NO_UTILITY
        deductions.append({"reason": "No utility service area at sampled point", "amount": _D_NO_UTILITY, "source": "07A+07B"})

    if wetland_found and not water_found and not sewer_found:
        insights.insert(0, Insight(
            title="Wetland + no utilities: compounded development risk",
            category="environmental",
            risk_level="High",
            finding="Mapped wetland signal detected AND no public utility service area at parcel.",
            buyer_meaning="This is the highest-risk combination for any build plan.",
            next_step="Do not make an offer without a professional wetland delineation and pre-application meeting with DOH on septic feasibility.",
            source_ids=["03", "07A", "07B"],
            confidence="screening",
        ))
        score += _D_WETLAND_SEPTIC
        deductions.append({"reason": "PATTERN 1: Wetland + no-utility compounded risk", "amount": _D_WETLAND_SEPTIC, "source": "03+07A+07B"})
    elif wetland_found and (water_found != sewer_found):
        score += _D_UTIL_GAP_WET
        deductions.append({"reason": "PATTERN 2: Partial utilities + wetland detected", "amount": _D_UTIL_GAP_WET, "source": "03+07A+07B"})

    if report.road and report.road.found:
        insights.append(Insight(
            title="County road record found",
            category="access",
            risk_level="Low",
            finding=f"Road record: {report.road.street or '—'}, maintained by {report.road.maintained_by or '—'}.",
            buyer_meaning="County road maintenance signal found. Does NOT verify legal ingress/egress or easements.",
            next_step="Verify legal access and frontage through title search.",
            source_ids=["08"],
            confidence="partial",
        ))
        score += _A_ROAD_FOUND
        additions.append({"reason": "County road record found", "amount": _A_ROAD_FOUND, "source": "08"})
    else:
        insights.append(Insight(
            title="No county road record found — access risk",
            category="access",
            risk_level="High",
            finding="No county road record returned for this parcel address.",
            buyer_meaning="No county road record found at this address. Parcel may lack confirmed legal access.",
            next_step="Verify access and easements in title search BEFORE making any offer.",
            source_ids=["08"],
            confidence="not_validated",
        ))
        score += _D_NO_ROAD
        deductions.append({"reason": "PATTERN 7: No road record — access risk", "amount": _D_NO_ROAD, "source": "08"})

    if report.permits:
        if not report.permits.issued_permits_match_found and not report.permits.certificate_of_occupancy_match_found:
            insights.append(Insight(
                title="No confirmed permit or CO records found in tested source",
                category="permits",
                risk_level="Unknown",
                finding="No confirmed records found in tested source.",
                buyer_meaning="No match found in county GIS permit layer. Search directly by folio for complete history.",
                next_step="Search HillsGovHub permit portal directly by folio/parcel number.",
                source_ids=["09", "10"],
                confidence="screening",
            ))
            score += _D_NO_PERMITS
            deductions.append({"reason": "No confirmed permit match in tested source", "amount": _D_NO_PERMITS, "source": "09+10"})

    if prop.just_value and prop.assessed_value and prop.just_value > 0:
        divergence = abs(prop.just_value - prop.assessed_value) / prop.just_value
        if divergence > 0.15:
            insights.append(Insight(
                title=f"Assessed value diverges {round(divergence * 100)}% from market value",
                category="tax",
                risk_level="Medium",
                finding=f"Just (market) value ${prop.just_value:,.0f} vs assessed ${prop.assessed_value:,.0f} — {round(divergence * 100)}% gap.",
                buyer_meaning="Significant divergence may indicate homestead or agricultural exemption that will be removed at sale.",
                next_step="Verify with Hillsborough Tax Collector whether this parcel carries an exemption that will be removed at sale.",
                source_ids=["01"],
                confidence="verified_api",
            ))
            score += _D_VALUE_GAP
            deductions.append({"reason": "PATTERN 3: Value/assessment gap > 15%", "amount": _D_VALUE_GAP, "source": "01"})

    addr_confirmed = any(e.source_id == "12" and e.status == "verified_api" for e in report.source_evidence)
    if addr_confirmed:
        score += _A_ADDR_CONFIRMED
        additions.append({"reason": "Official address confirmed in address point layer", "amount": _A_ADDR_CONFIRMED, "source": "12"})

    score = max(0, min(100, score))

    if   score >= 90: label = "Excellent"
    elif score >= 75: label = "Good"
    elif score >= 55: label = "Moderate"
    elif score >= 35: label = "Caution"
    else:             label = "High Risk"

    buildability = BuildabilityScore(score=score, label=label, deductions=deductions, additions=additions)
    return insights, buildability
