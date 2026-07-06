"""Statewide FL address type-ahead.

Backed by the Esri World Geocoder `suggest` endpoint (free, no token, built
for search-as-you-type). Earlier designs are infeasible:
  - FL DOR statewide cadastral: attribute LIKE queries time out (>25s)
  - Hillsborough address points: fast but one county only
Results are filtered to Florida and returned as plain formatted strings.
"""
from __future__ import annotations

import re

import httpx

_ESRI_SUGGEST_URL = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/suggest"
# Florida bounding box — keeps suggestions in-state
_FL_EXTENT = "-87.63,24.40,-79.97,31.10"
MAX_RESULTS = 8


def _sanitize(q: str) -> str:
    return re.sub(r"[\"';%]", "", q or "").strip()


async def suggest_addresses(q: str) -> list[str]:
    """Return up to 8 formatted FL address suggestions for partial query q."""
    q = _sanitize(q)
    if len(q) < 3:
        return []
    params = {
        "text": q,
        "f": "json",
        "countryCode": "USA",
        "searchExtent": _FL_EXTENT,
        "category": "Point Address,Street Address,Street Name",
        "maxSuggestions": 15,
    }
    async with httpx.AsyncClient(timeout=6.0) as client:
        resp = await client.get(_ESRI_SUGGEST_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    out: list[str] = []
    seen: set[str] = set()
    for s in data.get("suggestions", []):
        if s.get("isCollection"):
            continue
        text = (s.get("text") or "").strip()
        # Keep Florida only; drop the trailing ", USA"
        if ", FL," not in text and not text.endswith(", FL"):
            continue
        text = re.sub(r",\s*USA$", "", text)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) == MAX_RESULTS:
            break
    return out
