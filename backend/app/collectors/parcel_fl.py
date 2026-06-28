import httpx
try:
    from pyproj import Transformer
    _transformer = Transformer.from_crs('EPSG:3086', 'EPSG:4326', always_xy=True)
    _PYPROJ_OK = True
except Exception:
    _transformer = None
    _PYPROJ_OK = False

URL = "https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/Florida_Statewide_Cadastral/FeatureServer/0/query"
DEFAULT = {"found": False, "source": "FL DOR Cadastral", "geometry": []}


def convert_rings_to_latlng(rings: list) -> list:
    if not _PYPROJ_OK or not _transformer:
        return []
    try:
        converted = []
        for ring in rings:
            converted_ring = []
            for point in ring:
                if len(point) < 2:
                    continue
                x, y = point[0], point[1]
                lng, lat = _transformer.transform(x, y)
                converted_ring.append([lat, lng])
            converted.append(converted_ring)
        return converted
    except Exception as e:
        print(f"[ParcelFL] Coordinate conversion error: {e}")
        return []


async def get_parcel_data(lat: float, lng: float) -> dict:
    try:
        params = {
            "where": "1=1",
            "geometry": f"{lng},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "PARCEL_ID,OWN_NAME,PHY_ADDR1,PHY_CITY,DOR_UC,JV,LND_VAL,NO_BULDNG,SALE_PRC1,SALE_YR1,LND_SQFOOT",
            "returnGeometry": "true",
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

        feature = features[0]
        attrs = feature.get("attributes", {})
        sq_footage = attrs.get("LND_SQFOOT")
        acreage = round(sq_footage / 43560, 4) if sq_footage is not None else None

        # Extract and convert geometry
        geometry_rings = []
        raw_geo = feature.get("geometry", {})
        if raw_geo and "rings" in raw_geo:
            geometry_rings = convert_rings_to_latlng(raw_geo["rings"])

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
            "geometry": geometry_rings,
            "source": "FL DOR Cadastral",
        }
    except Exception as e:
        print(f"[ParcelFL] Error: {e}")
        return DEFAULT
