import httpx

URL = "https://services.arcgis.com/QVENGdaPbd4LUkLV/arcgis/rest/services/USFWS_Critical_Habitat/FeatureServer/0/query"
_CLEAN   = {"habitat_found": False, "species": [], "source": "USFWS Critical Habitat", "data_available": True}
_ERROR   = {"habitat_found": False, "species": [], "source": "USFWS Critical Habitat", "data_available": False}
BUFFER = 0.003  # ~200m — tight enough to stay parcel-local, wide enough to catch large polygons


async def get_critical_habitat(lat: float, lng: float) -> dict:
    try:
        params = {
            "where": "1=1",
            "geometry": f"{lng - BUFFER},{lat - BUFFER},{lng + BUFFER},{lat + BUFFER}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "comname,sciname,status",
            "returnGeometry": "false",
            "f": "json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        if "error" in data:
            return _ERROR

        features = data.get("features", [])
        if not features:
            return _CLEAN

        species = []
        for f in features:
            attrs = f.get("attributes", {})
            name = attrs.get("comname") or attrs.get("sciname")
            if name and name not in species:
                species.append(name)

        return {
            "habitat_found": True,
            "species": species,
            "source": "USFWS Critical Habitat",
            "data_available": True,
        }
    except Exception as e:
        print(f"[CriticalHabitat] Error: {e}")
        return _ERROR
