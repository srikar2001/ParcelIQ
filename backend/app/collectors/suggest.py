"""
Source 12 (suggest variant): Real-time address type-ahead.

Queries the Hillsborough County Official Address Point layer for
prefix/partial matches. Returns up to 10 ranked suggestions with
folio, full address, city, zip, and coordinates.

Design decisions:
- Uses FULLADDR LIKE '%q%' for substring matching (handles directionals)
- Also falls back to ADDRNUM = <n> for pure-integer queries so "4420"
  returns every "4420 <any street>" entry county-wide
- Returns geometry (x/y) from the address point for a reliable coordinate
  that belongs to this address — more accurate than parcel centroid for
  routing a user to the correct location
- resultRecordCount capped at 10 to keep response fast
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

import httpx

from app.core.config import settings


class SuggestResult:
    __slots__ = ("full_address", "folio", "strap", "city", "zip", "lat", "lon")

    def __init__(
        self,
        full_address: str,
        folio: Optional[str],
        strap: Optional[str],
        city: Optional[str],
        zip_code: Optional[str],
        lat: Optional[float],
        lon: Optional[float],
    ) -> None:
        self.full_address = full_address
        self.folio = folio
        self.strap = strap
        self.city = city
        self.zip = zip_code
        self.lat = lat
        self.lon = lon

    def to_dict(self) -> dict:
        return {
            "full_address": self.full_address,
            "folio": self.folio,
            "strap": self.strap,
            "city": self.city,
            "zip": self.zip,
            "lat": self.lat,
            "lon": self.lon,
        }


def _build_suggest_where(q: str) -> str:
    """Build WHERE clause for address point type-ahead."""
    safe = re.sub(r"[\"';]", "", q.strip()).upper()
    if not safe:
        return "1=0"
    # Pure integer — match by house number for "4420 <any street>" style
    if re.fullmatch(r"\d{1,6}", safe):
        return f"ADDRNUM = '{safe}'"
    # Otherwise substring match on full address
    return f"UPPER(FULLADDR) LIKE '%{safe}%'"


async def suggest_addresses(q: str) -> list[SuggestResult]:
    """Return up to 10 address suggestions for partial query q."""
    where = _build_suggest_where(q)
    if where == "1=0":
        return []

    params = {
        "where": where,
        "outFields": "FOLIO,STRAP,FULLADDR,PLACENAME,ZIP,POSTALCOMM",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": "10",
        "orderByFields": "FULLADDR ASC",
        "f": "json",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(settings.address_point_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    results: list[SuggestResult] = []
    seen_folios: set[str] = set()

    for feat in data.get("features", []):
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry") or {}
        folio = attrs.get("FOLIO")
        full_addr = attrs.get("FULLADDR") or ""
        if not full_addr:
            continue
        # Deduplicate by folio when same parcel has multiple address points
        if folio and folio in seen_folios:
            continue
        if folio:
            seen_folios.add(folio)

        city = attrs.get("POSTALCOMM") or attrs.get("PLACENAME") or ""
        results.append(
            SuggestResult(
                full_address=full_addr,
                folio=folio,
                strap=attrs.get("STRAP"),
                city=city.strip(),
                zip_code=attrs.get("ZIP"),
                lat=geom.get("y"),
                lon=geom.get("x"),
            )
        )

    return results[:10]
