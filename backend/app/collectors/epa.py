import httpx

URL = "https://echodata.epa.gov/echo/rest/services/ECHO/Superfund_Sites_FRS/MapServer/0/query"
DEFAULT = {"contamination_found": False, "sites": [], "source": "EPA ECHO Superfund"}


async def get_epa_superfund(lat: float, lng: float) -> dict:
    try:
        params = {
            "where": "1=1",
            "geometry": f"{lng},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "distance": "500",
            "units": "esriSRUnit_Meter",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FAC_NAME,SITE_TYPE,STATUS_FLAG",
            "returnGeometry": "false",
            "f": "json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        if "error" in data:
            return DEFAULT

        features = data.get("features", [])
        if not features:
            return DEFAULT

        sites = []
        for f in features:
            name = f.get("attributes", {}).get("FAC_NAME")
            if name and name not in sites:
                sites.append(name)

        return {
            "contamination_found": True,
            "sites": sites,
            "source": "EPA ECHO Superfund",
        }
    except Exception as e:
        print(f"[EPA] Error: {e}")
        return DEFAULT
