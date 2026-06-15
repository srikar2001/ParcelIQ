"""
Sources 09 / 10: Issued Permits + Certificate of Occupancy.

Per master prompt: NEVER claim "no permits exist" when no match is found.
The county GIS layer's address matching is unreliable. Tries:
  1. Narrow address match (house# + first street word)
  2. Folio-based match (PARCEL_NO LIKE '%{folio}%')
Always caps results at 10 to avoid 600+ record dumps.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import httpx

from app.core.config import settings
from app.models.schema import PermitsSection, SourceEvidence


async def _query_permits(url: str, address: Optional[str], folio: Optional[str]) -> list[dict]:
    matches: list[dict] = []

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        # Attempt 1: address-based LIKE
        if address:
            parts = address.strip().split()
            search_term = f"{parts[0]} {parts[1]}".upper() if len(parts) >= 2 else address.strip().upper()
            params = {
                "where": f"UPPER(ADDRESS) LIKE '%{search_term}%'",
                "outFields": "*",
                "returnGeometry": "false",
                "resultRecordCount": 10,
                "f": "json",
            }
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            matches = [f["attributes"] for f in data.get("features", [])]

        # Attempt 2: folio fallback if address search returned nothing
        if not matches and folio:
            safe_folio = folio.replace("'", "")
            params = {
                "where": f"PARCEL_NO LIKE '%{safe_folio}%'",
                "outFields": "*",
                "returnGeometry": "false",
                "resultRecordCount": 10,
                "f": "json",
            }
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                matches = [f["attributes"] for f in data.get("features", [])]
            except Exception:
                pass

    return matches[:10]


async def get_permits_info(
    address: Optional[str],
    folio: Optional[str] = None,
) -> tuple[PermitsSection, list[SourceEvidence]]:
    permits_matches = await _query_permits(settings.permits_url, address, folio)
    co_matches = await _query_permits(settings.co_url, address, folio)

    found_either = bool(permits_matches or co_matches)
    section = PermitsSection(
        issued_permits_match_found=len(permits_matches) > 0,
        certificate_of_occupancy_match_found=len(co_matches) > 0,
        notes=(
            "No confirmed records found in tested source."
            if not found_either
            else "Possible matches found — manual verification of exact address still required."
        ),
    )

    permits_evidence = SourceEvidence(
        source_id="09",
        source_name="Issued Permits",
        pulled_or_searched_date=str(date.today()),
        status="api_works_no_confirmed_match" if not permits_matches else "possible_match",
        confidence="screening",
        url=settings.permits_url,
        raw={"match_count": len(permits_matches)},
        notes=(
            "No exact address or folio match found in tested GIS layer; do not say no permits exist."
            if not permits_matches
            else f"{len(permits_matches)} possible record(s) found; verify exact parcel/address match manually."
        ),
    )

    co_evidence = SourceEvidence(
        source_id="10",
        source_name="Certificate of Occupancy",
        pulled_or_searched_date=str(date.today()),
        status="api_works_no_confirmed_match" if not co_matches else "possible_match",
        confidence="screening",
        url=settings.co_url,
        raw={"match_count": len(co_matches)},
        notes=(
            "No confirmed CO found in tested GIS layer."
            if not co_matches
            else f"{len(co_matches)} possible record(s) found; verify exact parcel/address match manually."
        ),
    )

    return section, [permits_evidence, co_evidence]
