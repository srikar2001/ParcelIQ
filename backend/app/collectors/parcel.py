"""
Source 01 / 04: HCPA Parcel Attributes + Geometry.

Supports search by address, folio, STRAP, or PIN. Returns candidate
parcels for the search step, and full attributes + geometry once a
folio is selected.

IMPORTANT: address search can return multiple records. Folio/STRAP/PIN
are the canonical identity once a parcel is selected — never reuse
demo values across requests.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

import httpx

from app.core.config import settings
from app.models.schema import ParcelCandidate, PropertySummary, SourceEvidence


FIELDS = (
    "FOLIO,STRAP,PIN,OWNER,SITE_ADDR,PAR_ACREAGE,DOR_DESC,"
    "JUST,LAND,EXF,ASD_VAL,TAX_VAL"
)


def _build_where(query: str) -> str:
    """
    Build an ArcGIS WHERE clause from a free-text query.

    Heuristics:
      - all-digits / contains a dot -> likely FOLIO
      - contains letters and is long -> likely STRAP or PIN
      - otherwise -> address search (SITE_ADDR LIKE)
    """
    q = query.strip().upper()

    if not q:
        return "1=0"

    # FOLIO is typically numeric with a dot, e.g. "8.0000" or "121923.0000"
    if all(c.isdigit() or c in ".-" for c in q):
        folio_clean = q.replace("-", "")
        if "." in q:
            # Has a decimal — definitely a folio
            return f"FOLIO = '{q}' OR FOLIO = '{folio_clean}'"
        # Pure integer (e.g. "4420") — could be a house number; search both
        safe = q.replace("'", "")
        return f"FOLIO = '{q}' OR FOLIO = '{folio_clean}' OR UPPER(SITE_ADDR) LIKE '{safe} %'"

    # STRAP / PIN: long alphanumeric strings WITHOUT spaces (addresses always have spaces)
    if len(q) >= 15 and any(c.isdigit() for c in q) and " " not in q:
        return f"STRAP = '{q}' OR PIN = '{q}'"

    # Fallback: address search — strip city/state suffix after comma if present
    addr = q.split(",")[0].strip()
    safe = addr.replace("'", "")
    return f"UPPER(SITE_ADDR) LIKE '%{safe}%'"


async def search_parcels(query: str) -> list[ParcelCandidate]:
    """Search HCPA parcel layer, returning candidate parcels."""
    params = {
        "where": _build_where(query),
        "outFields": FIELDS,
        "returnGeometry": "false",
        "resultRecordCount": "5",
        "f": "json",
    }

    last_exc: Exception = RuntimeError("no attempts made")
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(1.0 * attempt)
            try:
                resp = await client.get(settings.hcpa_parcel_url, params=params)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as exc:
                last_exc = exc
        else:
            raise last_exc

    candidates: list[ParcelCandidate] = []
    for feature in data.get("features", []):
        attrs = feature.get("attributes", {})
        folio = attrs.get("FOLIO")
        if not folio:
            continue
        candidates.append(
            ParcelCandidate(
                folio=str(folio),
                strap=attrs.get("STRAP"),
                pin=attrs.get("PIN"),
                address=attrs.get("SITE_ADDR"),
                owner=attrs.get("OWNER"),
                acreage=attrs.get("PAR_ACREAGE"),
            )
        )

    return candidates


async def get_parcel_attributes(folio: str) -> tuple[Optional[PropertySummary], Optional[SourceEvidence]]:
    """Fetch full attributes for a single parcel by FOLIO."""
    params = {
        "where": f"FOLIO = '{folio}'",
        "outFields": FIELDS,
        "returnGeometry": "false",
        "f": "json",
    }

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.hcpa_parcel_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        return None, SourceEvidence(
            source_id="01",
            source_name="HCPA Parcel Attributes",
            pulled_or_searched_date=str(date.today()),
            status="no_match",
            confidence="not_validated",
            url=settings.hcpa_parcel_url,
            notes=f"No parcel found for FOLIO {folio}",
        )

    attrs = features[0]["attributes"]

    # Split SITE_ADDR if it includes city/state/zip; HCPA SITE_ADDR is
    # typically the street address only. City/state/zip filled from
    # address point source (12) when available, defaults below.
    summary = PropertySummary(
        address=attrs.get("SITE_ADDR"),
        county="Hillsborough",
        state="FL",
        folio=str(attrs.get("FOLIO")),
        strap=attrs.get("STRAP"),
        pin=attrs.get("PIN"),
        owner=attrs.get("OWNER"),
        acreage_gross=attrs.get("PAR_ACREAGE"),
        use=attrs.get("DOR_DESC"),
        just_value=attrs.get("JUST"),
        land_value=attrs.get("LAND"),
        assessed_value=attrs.get("ASD_VAL"),
    )

    evidence = SourceEvidence(
        source_id="01",
        source_name="HCPA Parcel Attributes",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.hcpa_parcel_url,
        raw=attrs,
    )

    return summary, evidence


async def get_parcel_geometry(folio: str) -> tuple[Optional[dict], Optional[dict], Optional[SourceEvidence]]:
    """
    Fetch parcel geometry and centroid by FOLIO.

    Returns (geometry, centroid, evidence).
    geometry: GeoJSON-like dict with polygon rings in EPSG:4326
    centroid: {"lat": ..., "lon": ...}
    """
    params = {
        "where": f"FOLIO = '{folio}'",
        "returnGeometry": "true",
        "outSR": "4326",
        "outFields": "FOLIO",
        "f": "json",
    }

    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        resp = await client.get(settings.hcpa_parcel_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    if not features:
        return None, None, SourceEvidence(
            source_id="04",
            source_name="HCPA Parcel Geometry",
            pulled_or_searched_date=str(date.today()),
            status="no_match",
            confidence="not_validated",
            url=settings.hcpa_parcel_url,
            notes=f"No geometry found for FOLIO {folio}",
        )

    geometry = features[0].get("geometry")

    centroid = None
    centroid_method = "none"
    if geometry and geometry.get("rings"):
        try:
            from shapely.geometry import Polygon
            rings = geometry["rings"]
            # Use exterior ring (index 0) for representative_point
            exterior = rings[0]
            poly = Polygon([(pt[0], pt[1]) for pt in exterior])
            rp = poly.representative_point()
            centroid = {"lat": rp.y, "lon": rp.x}
            centroid_method = "shapely_representative_point"
        except Exception:
            # Fallback: bounding box center (better than vertex average)
            ring = geometry["rings"][0]
            lons = [pt[0] for pt in ring]
            lats = [pt[1] for pt in ring]
            centroid = {
                "lat": (min(lats) + max(lats)) / 2,
                "lon": (min(lons) + max(lons)) / 2,
            }
            centroid_method = "bbox_center_fallback"

    evidence = SourceEvidence(
        source_id="04",
        source_name="HCPA Parcel Geometry",
        pulled_or_searched_date=str(date.today()),
        status="verified_api",
        confidence="verified_api",
        url=settings.hcpa_parcel_url,
        notes=f"Polygon rings in EPSG:4326. Interior point method: {centroid_method}.",
    )

    return geometry, centroid, evidence
