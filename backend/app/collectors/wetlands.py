import asyncio
import httpx

NWI_URL = "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Wetlands/FeatureServer/0/query"
DEFAULT = {"wetland_on_parcel": False, "wetland_nearby": False, "wetland_type": None, "wetland_code": None, "source": "USFWS NWI"}
ERROR   = {"wetland_on_parcel": None,  "wetland_nearby": None,  "wetland_type": None, "wetland_code": None, "source": "USFWS NWI", "error": True}


async def _query(client: httpx.AsyncClient, lng: float, lat: float, buffer: float = 0) -> list:
    if buffer:
        geometry = f"{lng - buffer},{lat - buffer},{lng + buffer},{lat + buffer}"
        geometry_type = "esriGeometryEnvelope"
    else:
        geometry = f"{lng},{lat}"
        geometry_type = "esriGeometryPoint"
    params = {
        "where": "1=1",
        "geometry": geometry,
        "geometryType": geometry_type,
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "WETLAND_TYPE,ATTRIBUTE",
        "returnGeometry": "false",
        "f": "json",
    }
    resp = await client.get(NWI_URL, params=params)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"NWI error: {data['error']}")
    return data.get("features", [])


async def get_wetlands(lat: float, lng: float) -> dict:
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                # on_parcel uses ~50m buffer so edge wetlands (not at centroid) are caught
                on_parcel, nearby = await asyncio.gather(
                    _query(client, lng, lat, buffer=0.00045),
                    _query(client, lng, lat, buffer=0.001),
                )

            wetland_type = None
            wetland_code = None
            if on_parcel:
                attrs = on_parcel[0].get("attributes", {})
                wetland_type = attrs.get("WETLAND_TYPE")
                wetland_code = attrs.get("ATTRIBUTE")

            return {
                "wetland_on_parcel": len(on_parcel) > 0,
                "wetland_nearby":    len(nearby) > 0,
                "wetland_type":      wetland_type,
                "wetland_code":      wetland_code,
                "source":            "USFWS NWI",
            }
        except Exception as e:
            if attempt == 0:
                await asyncio.sleep(0.5)
                continue
            print(f"[Wetlands] Error after 2 attempts: {e}")
            return ERROR
    return ERROR
