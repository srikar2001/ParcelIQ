import httpx

FEMA_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
DEFAULT = {"zone": "UNKNOWN", "sfha": False, "source": "FEMA NFHL"}


async def get_flood_zone(lat: float, lng: float) -> dict:
    try:
        params = {
            "where": "1=1",
            "geometry": f"{lng},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE",
            "returnGeometry": "false",
            "f": "json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(FEMA_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        features = data.get("features", [])
        if not features:
            return DEFAULT

        attrs = features[0].get("attributes", {})
        return {
            "zone": attrs.get("FLD_ZONE") or "UNKNOWN",
            "zone_subtype": attrs.get("ZONE_SUBTY") or "",
            "sfha": attrs.get("SFHA_TF", "F") == "T",
            "source": "FEMA NFHL",
        }
    except Exception as e:
        print(f"[Flood] Error: {e}")
        return DEFAULT
