"""
Central configuration: source registry URLs and settings.

URLs sourced from 02_SOURCE_REGISTRY.json. Each entry maps to source_id
used in property_report.json source_evidence for traceability.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    http_timeout: float = 15.0
    mapbox_token: str = ""

    # ── External service credentials (optional — graceful fallback if missing)
    supabase_url: str = ""
    supabase_key: str = ""
    google_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Source 01 / 04: HCPA Parcel Attributes + Geometry
    hcpa_parcel_url: str = (
        "https://gis.tpcmaps.org/arcgis/rest/services/Parcels/MapServer/2/query"
    )

    # ── Source 02: FEMA NFHL Flood Hazard Zones
    fema_flood_url: str = (
        "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
    )

    # ── Source 03: USFWS / NWI Wetlands (ESRI-hosted public copy; original USFWS endpoint now auth-gated)
    nwi_wetlands_url: str = (
        "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Wetlands/FeatureServer/0/query"
    )

    # ── Source 05: Hillsborough Zoning
    zoning_url: str = (
        "https://gisdextweb1.hillsboroughcounty.org/arcgis/rest/services/Hosted/Zoning_and_Regulatory/FeatureServer/0/query"
    )

    # ── Source 06: Future Land Use
    future_lu_url: str = (
        "https://gis.tpcmaps.org/arcgis/rest/services/LandUse/FutureLU_HC/MapServer/2/query"
    )

    # ── Source 07A: Water Service Area
    water_service_url: str = (
        "https://gisdextweb1.hillsboroughcounty.org/arcgis/rest/services/Hosted/Utility_Service_Area/FeatureServer/0/query"
    )

    # ── Source 07B: Sewer Service Area
    sewer_service_url: str = (
        "https://gisdextweb1.hillsboroughcounty.org/arcgis/rest/services/Hosted/Utility_Service_Area/FeatureServer/1/query"
    )

    # ── Source 08: Road Authority / Maintenance
    roads_url: str = (
        "https://gisdextweb1.hillsboroughcounty.org/arcgis/rest/services/Hosted/Roads_and_Transportation/FeatureServer/1/query"
    )

    # ── Source 09: Issued Permits
    permits_url: str = (
        "https://maps.hillsboroughcounty.org/arcgis/rest/services/PermitsPlus/ResidentialCommericalIssuedPermitsCertOccMapService/FeatureServer/0/query"
    )

    # ── Source 10: Certificate of Occupancy
    co_url: str = (
        "https://maps.hillsboroughcounty.org/arcgis/rest/services/PermitsPlus/ResidentialCommericalIssuedPermitsCertOccMapService/FeatureServer/1/query"
    )

    # ── Source 11: Evacuation Zone
    evac_url: str = (
        "https://gisdextweb1.hillsboroughcounty.org/arcgis/rest/services/Hosted/HEAT_2026_Webmap/FeatureServer/1/query"
    )

    # ── Source 12: Official Address Points
    address_point_url: str = (
        "https://maps.hillsboroughcounty.org/arcgis/rest/services/DSD_Viewer_Services/DSD_Viewer_Address_Points_Numbers_Only/MapServer/0/query"
    )

    class Config:
        env_file = ".env"


settings = Settings()
