import asyncio
import httpx

FEMA_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
DEFAULT     = {"zone": "UNKNOWN",    "sfha": False, "source": "FEMA NFHL"}
NOT_MAPPED  = {"zone": "NOT_MAPPED", "sfha": False, "source": "FEMA NFHL"}
API_ERROR   = {"zone": "ERROR",      "sfha": False, "source": "FEMA NFHL"}

_PARAMS_BASE = {
    "where": "1=1",
    "geometryType": "esriGeometryPoint",
    "inSR": "4326",
    "spatialRel": "esriSpatialRelIntersects",
    "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE",
    "returnGeometry": "false",
    "f": "json",
}


async def _query(lat: float, lng: float) -> dict | None:
    params = {**_PARAMS_BASE, "geometry": f"{lng},{lat}"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(FEMA_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    if "error" in data:
        raise RuntimeError(f"FEMA error: {data['error']}")
    return data


async def get_flood_zone(lat: float, lng: float) -> dict:
    for attempt in range(2):
        try:
            data = await _query(lat, lng)
            features = data.get("features", [])
            if not features:
                return NOT_MAPPED

            attrs = features[0].get("attributes", {})
            zone_raw = attrs.get("FLD_ZONE")
            zone = (zone_raw or "").strip().upper() or "UNKNOWN"

            # SFHA_TF is "T" or "F" — None means the field was missing, treat as SFHA if zone says so
            sfha_raw = attrs.get("SFHA_TF")
            if sfha_raw is None:
                # Infer from zone: A, AE, VE, V, AO, AH are always SFHA
                sfha = zone in {"AE", "VE", "V", "AO", "AH", "A"}
            else:
                sfha = str(sfha_raw).strip().upper() == "T"

            return {
                "zone": zone,
                "zone_subtype": (attrs.get("ZONE_SUBTY") or "").strip(),
                "sfha": sfha,
                "source": "FEMA NFHL",
            }
        except Exception as e:
            if attempt == 0:
                await asyncio.sleep(1.0)
                continue
            print(f"[Flood] Error after 2 attempts: {e}")
            return API_ERROR
    return API_ERROR  # unreachable but satisfies type checker
