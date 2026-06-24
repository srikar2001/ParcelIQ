import httpx

URL = "https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/Florida_Statewide_Cadastral/FeatureServer/0/query"
DEFAULT = {"found": False, "source": "FL DOR Cadastral"}


async def get_parcel_data(lat: float, lng: float) -> dict:
    try:
        params = {
            "where": "1=1",
            "geometry": f"{lng},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "PARCEL_ID,OWN_NAME,PHY_ADDR1,PHY_CITY,DOR_UC,JV,LND_VAL,NO_BULDNG,SALE_PRC1,SALE_YR1,LND_SQFOOT",
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

        attrs = features[0].get("attributes", {})
        sq_footage = attrs.get("LND_SQFOOT")
        acreage = round(sq_footage / 43560, 4) if sq_footage else None

        return {
            "found": True,
            "parcel_id": attrs.get("PARCEL_ID"),
            "owner": attrs.get("OWN_NAME"),
            "acreage": acreage,
            "land_use_code": str(attrs.get("DOR_UC")) if attrs.get("DOR_UC") is not None else None,
            "just_value": attrs.get("JV"),
            "land_value": attrs.get("LND_VAL"),
            "building_count": attrs.get("NO_BULDNG") or 0,
            "last_sale_price": attrs.get("SALE_PRC1"),
            "last_sale_year": attrs.get("SALE_YR1"),
            "source": "FL DOR Cadastral",
        }
    except Exception as e:
        print(f"[ParcelFL] Error: {e}")
        return DEFAULT
