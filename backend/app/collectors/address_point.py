"""
Source 12: Official Address Point.

Confirms the canonical county address (STRAP, FOLIO, full address) for
a given address search. Used to validate/correct address details from
the HCPA parcel record (e.g. city/zip).
"""
from __future__ import annotations

from datetime import date

import httpx

from app.core.config import settings
from app.models.schema import SourceEvidence


async def get_address_point(address: str | None) -> tuple[dict | None, SourceEvidence]:
    if not address:
        evidence = SourceEvidence(
            source_id="12",
            source_name="Official Address Point",
            pulled_or_searched_date=str(date.today()),
            status="no_address",
            confidence="not_validated",
            url=settings.address_point_url,
            notes="No address available for lookup.",
        )
        return None, evidence

    parts = address.strip().split()
    search_term = " ".join(parts).upper().replace("'", "")

    # Build a LIKE clause tolerant of spacing, e.g. "19931%ANGEL"
    like_clause = "%".join(search_term.split())

    params = {
        "where": f"UPPER(FULLADDR) LIKE '%{like_clause}%'",
        "outFields": "STRAP,FOLIO,FULLADDR,MSAGFULLADDR,SVCLOC_NBR,SITEADDID",
        "returnGeometry": "false",
        "f": "json",
    }

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.address_point_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        evidence = SourceEvidence(
            source_id="12",
            source_name="Official Address Point",
            pulled_or_searched_date=str(date.today()),
            status="no_match",
            confidence="not_validated",
            url=settings.address_point_url,
            notes=f"No address point match for '{address}'.",
        )
        return None, evidence

    attrs = features[0]["attributes"]
    evidence = SourceEvidence(
        source_id="12",
        source_name="Official Address Point",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.address_point_url,
        raw=attrs,
        notes="Official county address confirmation.",
    )
    return attrs, evidence
