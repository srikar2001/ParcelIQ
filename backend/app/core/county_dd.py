"""County due-diligence checklist for a screened parcel.

Turns one parcel's screening result into the calls an investor still has to
make before an offer: who handles zoning, septic, wetlands permits, taxes and
title in THAT county, and what to ask each of them. Pure data + rules, no
upstream calls — every office URL below was checked live (HTTP 200 with a
matching page title, or a bot-blocked 403 on the office's own domain) on
2026-10-20. Offices with no verified site get a search link instead of a
guessed URL, so a link never lands on a parked or wrong domain.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

# pa = property appraiser, tc = tax collector, gov = county government
# (planning / zoning / building / floodplain administrator).
COUNTY_LINKS: dict[str, dict[str, str]] = {
    "Alachua": {"pa": "https://www.acpafl.org", "tc": "https://www.alachuacollector.com", "gov": "https://alachuacounty.us"},
    "Baker": {"pa": "https://www.bakerpa.com", "tc": "https://www.bakertaxcollector.com", "gov": "https://www.bakercountyfl.org"},
    "Bay": {"pa": "https://www.baypa.net", "tc": "https://baycountyfltax.gov", "gov": "https://www.baycountyfl.gov"},
    "Bradford": {"pa": "https://www.bradfordappraiser.com", "tc": "https://www.bradfordtaxcollector.com", "gov": "https://www.bradfordcountyfl.gov"},
    "Brevard": {"pa": "https://www.bcpao.us", "tc": "https://www.brevardtaxcollector.com", "gov": "https://www.brevardfl.gov"},
    "Broward": {"pa": "https://web.bcpa.net", "tc": "https://www.broward.org/RecordsTaxesTreasury", "gov": "https://www.broward.org"},
    "Calhoun": {"pa": "https://www.calhounpa.net", "tc": "https://www.calhouncountytaxcollector.com", "gov": "https://calhouncountygov.com"},
    "Charlotte": {"pa": "https://www.ccappraiser.com", "gov": "https://www.charlottecountyfl.gov"},
    "Citrus": {"pa": "https://www.citruspa.org", "gov": "https://www.citrusbocc.com"},
    "Clay": {"pa": "https://ccpao.com", "tc": "https://www.claycountytax.com", "gov": "https://www.claycountygov.com"},
    "Collier": {"pa": "https://www.collierappraiser.com", "tc": "https://www.colliertaxcollector.com", "gov": "https://www.collier.gov"},
    "Columbia": {"pa": "https://www.ccpafl.com", "tc": "https://www.columbiataxcollector.com", "gov": "https://www.columbiacountyfla.com"},
    "DeSoto": {"pa": "https://www.desotopa.com", "tc": "https://www.desotocountytaxcollector.com", "gov": "https://www.desotobocc.com"},
    "Dixie": {"pa": "https://www.qpublic.net/fl/dixie"},
    "Duval": {"pa": "https://www.coj.net/departments/property-appraiser", "tc": "https://www.duvaltaxcollector.net", "gov": "https://www.coj.net"},
    "Escambia": {"pa": "https://www.escpa.org", "tc": "https://www.escambiataxcollector.com", "gov": "https://myescambia.com"},
    "Flagler": {"pa": "https://www.flaglerpa.com", "tc": "https://www.flaglertax.com", "gov": "https://www.flaglercounty.gov"},
    "Franklin": {"pa": "https://franklincountypa.net", "tc": "https://www.franklincountytaxcollector.com", "gov": "https://www.franklincountyflorida.com"},
    "Gadsden": {"pa": "https://www.gadsdenpa.com", "tc": "https://www.gadsdentaxcollector.com", "gov": "https://www.gadsdencountyfl.gov"},
    "Gilchrist": {"gov": "https://gilchrist.fl.us"},
    "Glades": {"gov": "https://www.myglades.com"},
    "Gulf": {"pa": "https://www.gulfpa.com", "tc": "https://www.gulfcountytaxcollector.com", "gov": "https://www.gulfcounty-fl.gov"},
    "Hamilton": {"pa": "https://www.hamiltonpa.com", "tc": "https://www.hamiltoncountytaxcollector.com", "gov": "https://www.hamiltoncountyfl.com"},
    "Hardee": {"pa": "https://www.hardeepa.com", "tc": "https://www.hardeecountytaxcollector.com", "gov": "https://www.hardeecounty.net"},
    "Hendry": {"pa": "https://www.hendryprop.com", "gov": "https://www.hendryfla.net"},
    "Hernando": {"pa": "https://www.hernandopa-fl.us", "gov": "https://www.hernandocounty.us"},
    "Highlands": {"pa": "https://www.hcpao.org", "tc": "https://www.hctaxcollector.com", "gov": "https://www.highlandsfl.gov"},
    "Hillsborough": {"pa": "https://www.hcpafl.org", "tc": "https://www.hillstax.org", "gov": "https://www.hillsboroughcounty.org"},
    "Holmes": {"tc": "https://www.holmescountytaxcollector.com"},
    "Indian River": {"tc": "https://www.irctax.com", "gov": "https://www.ircgov.com"},
    "Jackson": {"pa": "https://jacksonpa.com", "tc": "https://www.jacksoncountytaxcollector.com", "gov": "https://www.jacksoncountyfl.gov"},
    "Jefferson": {"pa": "https://www.jeffersonpa.net", "tc": "https://www.jeffersoncountytaxcollector.com", "gov": "https://www.jeffersoncountyfl.gov"},
    "Lafayette": {"pa": "https://www.lafayettepa.com", "tc": "https://www.lafayettetc.com", "gov": "https://www.lafayettecountyfl.net"},
    "Lake": {"pa": "https://www.lakecopropappr.com", "tc": "https://www.laketax.com", "gov": "https://www.lakecountyfl.gov"},
    "Lee": {"pa": "https://www.leepa.org", "tc": "https://www.leetc.com", "gov": "https://www.leefl.gov"},
    "Leon": {"pa": "https://www.leonpa.gov", "tc": "https://www.leontaxcollector.net", "gov": "https://cms.leoncountyfl.gov"},
    "Levy": {"pa": "https://www.levypa.com", "tc": "https://www.levytaxcollector.com", "gov": "https://www.levycounty.org"},
    "Liberty": {"pa": "https://www.libertypa.org", "tc": "https://www.libertycountytaxcollector.com", "gov": "https://www.libertycountyfl.org"},
    "Madison": {"pa": "https://www.madisonpa.com", "tc": "https://www.madisoncountytaxcollector.com", "gov": "https://www.madisoncountyfl.com"},
    "Manatee": {"pa": "https://www.manateepao.gov", "tc": "https://www.taxcollector.com", "gov": "https://www.mymanatee.org"},
    "Marion": {"pa": "https://www.pa.marion.fl.us", "tc": "https://www.mariontax.com", "gov": "https://www.marionfl.org"},
    "Martin": {"pa": "https://www.pa.martin.fl.us", "tc": "https://www.martintaxcollector.com", "gov": "https://www.martin.fl.us"},
    "Miami-Dade": {"pa": "https://www.miamidade.gov/pa", "tc": "https://www.miamidade.gov/taxcollector", "gov": "https://www.miamidade.gov"},
    "Monroe": {"pa": "https://www.mcpafl.org", "tc": "https://www.monroetaxcollector.com", "gov": "https://www.monroecounty-fl.gov"},
    "Nassau": {"tc": "https://www.nassautaxes.com", "gov": "https://www.nassaucountyfl.com"},
    "Okaloosa": {"pa": "https://www.okaloosapa.com", "tc": "https://www.okaloosatax.com", "gov": "https://myokaloosa.com"},
    "Okeechobee": {"pa": "https://www.okeechobeepa.com", "tc": "https://www.okeechobeecountytaxcollector.com", "gov": "https://www.okeechobeecountyfl.gov"},
    "Orange": {"pa": "https://www.ocpafl.org", "tc": "https://www.octaxcol.com", "gov": "https://www.orangecountyfl.net"},
    "Osceola": {"pa": "https://www.property-appraiser.org", "tc": "https://www.osceolataxcollector.org", "gov": "https://www.osceola.org"},
    "Palm Beach": {"pa": "https://www.pbcpao.gov", "tc": "https://www.pbctax.gov", "gov": "https://discover.pbcgov.org"},
    "Pasco": {"pa": "https://www.pascopa.com", "tc": "https://www.pascotaxes.com", "gov": "https://www.pascocountyfl.net"},
    "Pinellas": {"pa": "https://www.pcpao.gov", "tc": "https://taxcollect.com", "gov": "https://pinellas.gov"},
    "Polk": {"pa": "https://www.polkpa.org", "tc": "https://www.polktaxes.com"},
    "Putnam": {"tc": "https://www.putnamtax.com", "gov": "https://www.putnam-fl.com"},
    "Santa Rosa": {"pa": "https://www.srcpa.gov", "tc": "https://www.srctc.com", "gov": "https://www.santarosa.fl.gov"},
    "Sarasota": {"pa": "https://www.sc-pa.com", "tc": "https://www.sarasotataxcollector.gov", "gov": "https://www.scgov.net"},
    "Seminole": {"pa": "https://www.scpafl.org", "tc": "https://www.seminoletax.org", "gov": "https://www.seminolecountyfl.gov"},
    "St. Johns": {"pa": "https://www.sjcpa.gov", "tc": "https://www.sjctax.us", "gov": "https://www.sjcfl.us"},
    "St. Lucie": {"pa": "https://www.paslc.gov", "tc": "https://www.tcslc.com", "gov": "https://www.stlucieco.gov"},
    "Sumter": {"pa": "https://www.sumterpa.com", "tc": "https://www.sumtertaxcollector.com", "gov": "https://www.sumtercountyfl.gov"},
    "Suwannee": {"pa": "https://www.suwanneepa.com", "gov": "https://www.suwanneecountyfl.gov"},
    "Taylor": {"pa": "https://www.qpublic.net/fl/taylor", "tc": "https://www.taylorcountytaxcollector.com", "gov": "https://www.taylorcountygov.com"},
    "Union": {"tc": "https://www.unioncountytc.com", "gov": "https://www.unioncounty-fl.gov"},
    "Volusia": {"pa": "https://vcpa.vcgov.org", "tc": "https://www.vctaxcollector.org", "gov": "https://www.volusia.org"},
    "Wakulla": {"pa": "https://www.mywakullapa.com", "tc": "https://www.wakullacountytaxcollector.com", "gov": "https://www.mywakulla.com"},
    "Walton": {"pa": "https://www.waltonpa.com", "tc": "https://www.waltontaxcollector.com", "gov": "https://www.co.walton.fl.us"},
    "Washington": {"tc": "https://www.washingtoncountytaxcollector.com", "gov": "https://www.washingtonfl.com"},
}

# Water management district(s) per county, primary first. Counties on a
# district boundary are split — the district that covers the parcel's own
# basin issues its Environmental Resource Permit (ERP).
_NW, _SR, _SJ, _SW, _SF = "NWFWMD", "SRWMD", "SJRWMD", "SWFWMD", "SFWMD"
WMD_INFO: dict[str, tuple[str, str]] = {
    _NW: ("Northwest Florida Water Management District", "https://nwfwater.com"),
    _SR: ("Suwannee River Water Management District", "https://www.mysuwanneeriver.com"),
    _SJ: ("St. Johns River Water Management District", "https://www.sjrwmd.com"),
    _SW: ("Southwest Florida Water Management District", "https://www.swfwmd.state.fl.us"),
    _SF: ("South Florida Water Management District", "https://www.sfwmd.gov"),
}
COUNTY_WMD: dict[str, tuple[str, ...]] = {
    "Escambia": (_NW,), "Santa Rosa": (_NW,), "Okaloosa": (_NW,), "Walton": (_NW,),
    "Holmes": (_NW,), "Washington": (_NW,), "Bay": (_NW,), "Jackson": (_NW,),
    "Calhoun": (_NW,), "Gulf": (_NW,), "Gadsden": (_NW,), "Liberty": (_NW,),
    "Franklin": (_NW,), "Leon": (_NW,), "Wakulla": (_NW,), "Jefferson": (_NW, _SR),
    "Madison": (_SR,), "Taylor": (_SR,), "Hamilton": (_SR,), "Suwannee": (_SR,),
    "Lafayette": (_SR,), "Dixie": (_SR,), "Columbia": (_SR,), "Gilchrist": (_SR,),
    "Union": (_SR, _SJ), "Bradford": (_SR, _SJ), "Levy": (_SR, _SW), "Baker": (_SJ, _SR),
    "Alachua": (_SJ, _SR), "Nassau": (_SJ,), "Duval": (_SJ,), "Clay": (_SJ,),
    "St. Johns": (_SJ,), "Putnam": (_SJ,), "Flagler": (_SJ,), "Volusia": (_SJ,),
    "Seminole": (_SJ,), "Brevard": (_SJ,), "Indian River": (_SJ,),
    "Marion": (_SJ, _SW), "Lake": (_SJ, _SW), "Orange": (_SJ, _SF), "Osceola": (_SF, _SJ),
    "Okeechobee": (_SF, _SJ), "Citrus": (_SW,), "Hernando": (_SW,), "Pasco": (_SW,),
    "Pinellas": (_SW,), "Hillsborough": (_SW,), "Manatee": (_SW,), "Sarasota": (_SW,),
    "DeSoto": (_SW,), "Hardee": (_SW,), "Sumter": (_SW,), "Polk": (_SW, _SF),
    "Highlands": (_SW, _SF), "Charlotte": (_SW, _SF), "Miami-Dade": (_SF,),
    "Broward": (_SF,), "Palm Beach": (_SF,), "Monroe": (_SF,), "Collier": (_SF,),
    "Lee": (_SF,), "Hendry": (_SF,), "Glades": (_SF,), "Martin": (_SF,), "St. Lucie": (_SF,),
}

STATE_LINKS = {
    "clerk": "https://www.flclerks.com/page/FindaClerk",
    "fema": "https://msc.fema.gov/portal/home",
    "dep_septic": "https://floridadep.gov/water/onsite-sewage",
    "dep_erp": "https://floridadep.gov/water/submerged-lands-environmental-resources-coordination/content/environmental-resource-permitting",
    "fwc_tortoise": "https://myfwc.com/license/wildlife/gopher-tortoise-permits/",
    "usfws": "https://www.fws.gov/office/florida-ecological-services",
}

_ALIASES = {
    "dade": "Miami-Dade", "miami dade": "Miami-Dade", "miamidade": "Miami-Dade",
    "de soto": "DeSoto", "saint johns": "St. Johns", "st johns": "St. Johns",
    "saint lucie": "St. Lucie", "st lucie": "St. Lucie",
}
_BY_KEY = {re.sub(r"[^a-z]", "", c.lower()): c for c in COUNTY_LINKS}


def normalize_county(name: str | None) -> str | None:
    """'LEVY', 'Levy County', 'st johns', 'Dade' -> canonical county name, else None."""
    if not name:
        return None
    s = re.sub(r"\s+county\s*(,?\s*fl(orida)?)?$", "", str(name).strip(), flags=re.I).strip().lower()
    s = s.replace(".", "")
    if s in _ALIASES:
        return _ALIASES[s]
    return _BY_KEY.get(re.sub(r"[^a-z]", "", s))


def _search(q: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(q)


def _doh_url(county: str) -> str:
    # Every county health department lives at <name>.floridahealth.gov
    # (all 67 checked live; unknown subdomains do not resolve).
    return "https://" + re.sub(r"[^a-z]", "", county.lower()) + ".floridahealth.gov"


def county_offices(county: str) -> list[dict]:
    """The offices an investor calls for a parcel in `county`, each with a URL
    and `verified` (False = a search link because no official site was confirmed)."""
    links = COUNTY_LINKS.get(county, {})

    def office(key: str, name: str, url_key: str | None, query: str, handles: str) -> dict:
        url = links.get(url_key) if url_key else None
        return {"key": key, "name": name, "url": url or _search(query),
                "verified": bool(url), "handles": handles}

    out = [
        office("pa", f"{county} County Property Appraiser", "pa",
               f"{county} County Florida property appraiser", "Ownership, legal description, land use code, assessed value"),
        office("tc", f"{county} County Tax Collector", "tc",
               f"{county} County Florida tax collector", "Delinquent taxes, tax certificates, payoff amounts"),
        {"key": "clerk", "name": f"{county} County Clerk of Court (Official Records)",
         "url": STATE_LINKS["clerk"], "verified": True,
         "handles": "Deeds, liens, recorded easements, deed restrictions, lis pendens"},
        office("gov", f"{county} County Planning, Zoning & Building", "gov",
               f"{county} County Florida planning and zoning", "Zoning, future land use, permits, floodplain rules, road maintenance"),
        {"key": "doh", "name": f"Florida Dept. of Health in {county} County (Environmental Health)",
         "url": _doh_url(county), "verified": True,
         "handles": "Septic (OSTDS) site evaluations and permits, private well permits"},
    ]
    for code in COUNTY_WMD.get(county, ()):
        nm, url = WMD_INFO[code]
        out.append({"key": "wmd_" + code.lower(), "name": nm + " (" + code + ")", "url": url, "verified": True,
                    "handles": "Environmental Resource Permits (wetlands, stormwater, fill), well construction permits"})
    return out


_FLOOD_RE = re.compile(r"\b(?:flood|FEMA)\s+zone\s+(AE|AO|AH|A\d*|VE|V\d*|X|D)\b", re.I)


def _flood_zone(result: dict) -> str | None:
    pi = result.get("parcel_info") or {}
    for text in list(result.get("flags") or []) + list(result.get("positives") or []):
        m = _FLOOD_RE.search(str(text))
        if m:
            return m.group(1).upper()
    sub = pi.get("flood_zone_subtype")
    return str(sub).upper() if sub else None


def _city(address: str | None) -> str | None:
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    if len(parts) >= 3 and re.match(r"^FL\b", parts[-1], re.I):
        return parts[-2]
    return None


def build_checklist(result: dict) -> dict:
    """Parcel-specific due-diligence checklist. `result` is one screening
    result (address, verdict, flags, positives, parcel_info)."""
    pi = result.get("parcel_info") or {}
    county = normalize_county(pi.get("county"))
    if not county:
        return {"county": None, "offices": [], "items": [],
                "note": "County unknown for this parcel, so no county checklist could be built."}

    offices = county_offices(county)
    wmd_keys = [o["key"] for o in offices if o["key"].startswith("wmd_")]
    apn = pi.get("parcel_id") or pi.get("apn")
    items: list[dict] = []

    def add(group: str, office: str, title: str, ask: str, priority: str = "normal", why: str | None = None, link: str | None = None):
        # Stable id (not an index) so a viewer's ticks stay on the same
        # question when a re-screen adds or removes other items.
        slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"\d", "", title.lower())).strip("-")[:48]
        items.append({"id": f"{office}:{slug}", "group": group, "office": office, "title": title,
                      "ask": ask, "priority": priority, "why": why, "link": link})

    # Title & taxes — every parcel.
    add("Title & taxes", "pa", "Confirm owner, legal description and land use code",
        f"Pull parcel {apn} and match the owner and legal description to your seller." if apn
        else "Look up the parcel and match the owner and legal description to your seller.")
    add("Title & taxes", "tc", "Check for unpaid taxes or tax certificates",
        "Are taxes current? Any outstanding tax certificates or a pending tax deed sale?")
    add("Title & taxes", "clerk", "Search official records for liens and restrictions",
        "Search the owner's name and parcel for liens, judgments, lis pendens, recorded easements and deed restrictions; order a title search before closing.")

    # Zoning & building.
    city = _city(result.get("address"))
    add("Zoning & building", "gov", "Confirm zoning and future land use allow your plan",
        "What is the zoning and future land use? Is a single-family home (or mobile home) allowed by right? Minimum lot size, setbacks, and is this a legal buildable lot?",
        why=(f"If the parcel is inside {city} city limits, the city — not the county — handles zoning and permits." if city else None))
    add("Zoning & building", "gov", "Ask about open code cases and permit history",
        "Any open code enforcement cases, unpaid fines/liens, or expired permits on this parcel?")

    # Septic & water.
    septic = pi.get("septic_suitable")
    drainage = pi.get("soil_drainage")
    if septic is False:
        add("Septic & water", "doh", "Soil looks poor for septic — confirm before offering",
            "Will this lot pass a septic site evaluation? If not, is a mound/engineered system allowed and what does it typically cost? Is central sewer available?",
            priority="high", why=f"Mapped soil: {drainage}." if drainage else "Mapped soil is rated poorly suited for septic.")
    else:
        add("Septic & water", "doh", "Confirm a septic permit is likely",
            "What does a septic site evaluation (soil/perc test) require here, and what is the typical wait? Is central sewer available instead?",
            why=(f"Mapped soil: {drainage} — looks septic-feasible, but only a site evaluation confirms it." if septic is True and drainage else None),
            link=STATE_LINKS["dep_septic"])
    add("Septic & water", "gov", "Check water service or well requirements",
        "Does county/city water reach this lot? If not, budget for a private well (permit from the water management district / health department).")

    # Environmental.
    zone = _flood_zone(result)
    if zone and zone[0] in ("A", "V"):
        add("Environmental", "gov", f"Flood Zone {zone}: get the floodplain rules",
            "Ask the floodplain administrator for the Base Flood Elevation, required finished-floor height, and fill/elevation rules; get a flood insurance quote.",
            priority="high", why=(f"Base Flood Elevation on file: {pi.get('base_flood_elevation_ft')} ft." if pi.get("base_flood_elevation_ft") else None),
            link=STATE_LINKS["fema"])
    wet = pi.get("wetland_type")
    if wet:
        add("Environmental", wmd_keys[0] if wmd_keys else "gov", "Wetlands mapped here: ask about an ERP",
            "Do wetlands on this parcel need an Environmental Resource Permit before clearing, fill or a driveway? Consider a wetland delineation to find the buildable area.",
            priority="high", why=f"Mapped wetland: {wet}.", link=STATE_LINKS["dep_erp"])
    species = [s for s in (pi.get("habitat_species") or []) if s]
    if species:
        tort = any("tortoise" in str(s).lower() for s in species)
        add("Environmental", "gov", "Protected species habitat: check survey rules",
            ("Gopher tortoise burrows need an FWC survey and relocation permit before any land clearing. " if tort else "")
            + "Ask whether a wildlife survey is required before permits are issued.",
            priority="high", why="Critical habitat mapped: " + ", ".join(map(str, species[:3])) + ".",
            link=STATE_LINKS["fwc_tortoise"] if tort else STATE_LINKS["usfws"])
    sites = pi.get("contamination_sites") or []
    if sites:
        add("Environmental", "gov", "Contamination record nearby: consider a Phase I ESA",
            "A Phase I Environmental Site Assessment checks whether a nearby EPA record affects this parcel; lenders often require one.",
            priority="high", why=f"{len(sites)} EPA Superfund record(s) within the search radius.")
    if pi.get("easement_found"):
        add("Title & taxes", "clerk", "Easement on the parcel: read the recorded document",
            "Find the recorded easement and check its width, who holds it, and whether it blocks a building site or driveway.",
            priority="high", why=f"Easement detected: {pi.get('easement_type') or 'type unknown'}.")

    # Access & utilities.
    rd = pi.get("road_distance_m")
    if pi.get("road_private") or (rd is not None and rd > 30):
        add("Access & utilities", "clerk", "Confirm legal road access",
            "Is there a recorded access easement to a public road? Landlocked lots are hard to finance and build on.",
            priority="high", why=(f"Nearest road is {rd} m away" + (" and private." if pi.get("road_private") else ".")) if rd is not None else "Nearest road is private.")
    if pi.get("road_name") or pi.get("road_type"):
        add("Access & utilities", "gov", "Ask whether the road is county-maintained",
            "Is the road a county-maintained public road? Dirt/private roads can mean maintenance costs and permit conditions.",
            why=" ".join(x for x in [pi.get("road_name"), f"({pi.get('road_surface')})" if pi.get("road_surface") else None] if x) or None)
    if pi.get("powerline_nearby") is False or (pi.get("powerline_distance") and ">" in str(pi.get("powerline_distance"))):
        add("Access & utilities", "gov", "Get a cost to bring power to the lot",
            "Ask the local electric utility for the cost and timeline to extend service to this parcel.",
            why=f"Nearest power line: {pi.get('powerline_distance')}." if pi.get("powerline_distance") else None)

    order = {"high": 0, "normal": 1}
    items.sort(key=lambda i: order.get(i["priority"], 1))  # stable: groups keep their order
    return {"county": county, "offices": offices, "items": items,
            "high_count": sum(1 for i in items if i["priority"] == "high")}
