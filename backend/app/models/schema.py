"""
Pydantic models for ParcelIQ.

Mirrors 06_DATA_MODEL_SCHEMA.json and the structure of
03_SAMPLE_PROPERTY_REPORT_19931_ANGEL_LN.json.

Confidence levels (per schema):
  verified_api | manual_search | historical_record | screening | partial | not_validated

Risk levels:
  High | Medium | Low | Positive | Unknown
"""
from __future__ import annotations

from typing import Optional, Literal
from pydantic import BaseModel, Field


ConfidenceLevel = Literal[
    "verified_api",
    "manual_search",
    "historical_record",
    "screening",
    "partial",
    "not_validated",
]

RiskLevel = Literal["High", "Medium", "Low", "Positive", "Unknown"]


# ──────────────────────────────────────────────────────────────────────────
# Source evidence (required for every visible claim)
# ──────────────────────────────────────────────────────────────────────────

class SourceEvidence(BaseModel):
    source_id: str
    source_name: str
    pulled_or_searched_date: str
    status: str
    confidence: ConfidenceLevel
    url: Optional[str] = None
    raw: Optional[dict] = None
    notes: Optional[str] = None
    public_verify_url: Optional[str] = None
    query_used: Optional[str] = None


class SuggestResult(BaseModel):
    full_address: str
    folio: Optional[str] = None
    strap: Optional[str] = None
    city: Optional[str] = None
    zip: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class SuggestResponse(BaseModel):
    query: str
    results: list[SuggestResult]


# ──────────────────────────────────────────────────────────────────────────
# Parcel identity / search
# ──────────────────────────────────────────────────────────────────────────

class ParcelCandidate(BaseModel):
    folio: str
    strap: Optional[str] = None
    pin: Optional[str] = None
    address: Optional[str] = None
    owner: Optional[str] = None
    acreage: Optional[float] = None


class SearchResponse(BaseModel):
    query: str
    candidates: list[ParcelCandidate]


# ──────────────────────────────────────────────────────────────────────────
# Property summary
# ──────────────────────────────────────────────────────────────────────────

class PropertySummary(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    county: str = "Hillsborough"
    folio: str
    strap: Optional[str] = None
    pin: Optional[str] = None
    owner: Optional[str] = None
    acreage_gross: Optional[float] = None
    use: Optional[str] = None
    just_value: Optional[float] = None
    land_value: Optional[float] = None
    assessed_value: Optional[float] = None
    geometry: Optional[dict] = None  # GeoJSON-ish polygon rings
    centroid: Optional[dict] = None  # {lat, lon}


# ──────────────────────────────────────────────────────────────────────────
# Environmental
# ──────────────────────────────────────────────────────────────────────────

class FloodInfo(BaseModel):
    fld_zone: Optional[str] = None
    zone_subty: Optional[str] = None
    sfha: Optional[bool] = None
    static_bfe: Optional[float] = None


class WetlandInfo(BaseModel):
    found: bool = False
    attribute: Optional[str] = None
    wetland_type: Optional[str] = None
    system: Optional[str] = None
    wetland_class: Optional[str] = None
    water_regime: Optional[str] = None
    polygon_acres: Optional[float] = None  # NOTE: polygon acres, not parcel acreage


class EvacZoneInfo(BaseModel):
    found: bool = False
    zone: Optional[str] = None


class EnvironmentalSection(BaseModel):
    flood: Optional[FloodInfo] = None
    wetlands: Optional[WetlandInfo] = None
    evacuation_zone: Optional[EvacZoneInfo] = None


# ──────────────────────────────────────────────────────────────────────────
# Zoning / Future Land Use
# ──────────────────────────────────────────────────────────────────────────

class ZoningInfo(BaseModel):
    nzone: Optional[str] = None
    nzone_desc: Optional[str] = None
    category: Optional[str] = None
    # zoning polygon acres deliberately NOT exposed as parcel acreage (per 05_UI_MAPPING)


class FutureLandUseInfo(BaseModel):
    flue: Optional[str] = None
    flu_desc: Optional[str] = None
    jurisdiction: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────

class ServiceAreaInfo(BaseModel):
    found: bool = False
    service_by: Optional[str] = None


class UtilitiesSection(BaseModel):
    water_service_area: Optional[ServiceAreaInfo] = None
    sewer_service_area: Optional[ServiceAreaInfo] = None


# ──────────────────────────────────────────────────────────────────────────
# Roads
# ──────────────────────────────────────────────────────────────────────────

class RoadInfo(BaseModel):
    found: bool = False
    street: Optional[str] = None
    authority: Optional[str] = None
    owner: Optional[str] = None
    maintained_by: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Permits / CO
# ──────────────────────────────────────────────────────────────────────────

class PermitsSection(BaseModel):
    issued_permits_match_found: bool = False
    certificate_of_occupancy_match_found: bool = False
    notes: str = "No confirmed records found in tested source."


# ──────────────────────────────────────────────────────────────────────────
# Top findings / insights (Insight Engine output)
# ──────────────────────────────────────────────────────────────────────────

class Insight(BaseModel):
    title: str
    category: str
    risk_level: RiskLevel
    finding: str
    buyer_meaning: str
    next_step: str
    source_ids: list[str]
    confidence: ConfidenceLevel


class BuildabilityScore(BaseModel):
    score: int = Field(ge=0, le=100)
    label: Literal["Excellent", "Good", "Moderate", "Caution", "High Risk"]
    deductions: list[dict] = []
    additions: list[dict] = []


# ──────────────────────────────────────────────────────────────────────────
# Full report
# ──────────────────────────────────────────────────────────────────────────

class PropertyReport(BaseModel):
    property: PropertySummary
    environmental: EnvironmentalSection
    zoning: Optional[ZoningInfo] = None
    future_land_use: Optional[FutureLandUseInfo] = None
    utilities: Optional[UtilitiesSection] = None
    road: Optional[RoadInfo] = None
    permits: Optional[PermitsSection] = None
    manual_sources: list[SourceEvidence] = []
    source_evidence: list[SourceEvidence] = []


class BuyerInsightsReport(BaseModel):
    folio: str
    buildability: BuildabilityScore
    top_findings: list[Insight]


# ──────────────────────────────────────────────────────────────────────────
# Batch screening
# ──────────────────────────────────────────────────────────────────────────

class ParcelInput(BaseModel):
    address: str
    apn: Optional[str] = None


class BatchRequest(BaseModel):
    parcels: list[ParcelInput]
    state: str = "FL"


class ParcelResult(BaseModel):
    address: str
    verdict: str  # KILL | REVIEW | PURSUE | ERROR
    score: Optional[int] = None
    auto_kill: bool = False
    auto_kill_reason: Optional[str] = None
    flags: list[str] = []
    positives: list[str] = []
    parcel_info: dict = {}
    sources: list[str] = []
    error: Optional[str] = None


class BatchResponse(BaseModel):
    total: int
    kills: int
    reviews: int
    pursues: int
    results: list[ParcelResult]
