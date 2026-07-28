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


async def get_parcel_geometry_by_id(parcel_id: str):
    """Look up a parcel's boundary + center by its PARCEL_ID (APN). Used to
    backfill geometry for older shared-parcel snapshots that were saved without
    it, so the shared Deal Review renders the REAL map. Free cadastral query
    (outSR=4326 so no pyproj needed) — returns {'geometry': [[lat,lng]...],
    'lat', 'lng'} or None."""
    if not parcel_id:
        return None
    safe_id = str(parcel_id).replace("'", "")
    params = {
        "where": f"PARCEL_ID='{safe_id}'",
        "outFields": "PARCEL_ID",
        "returnGeometry": "true",
        "returnCentroid": "true",
        "outSR": "4326",
        "resultRecordCount": "1",
        "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(URL, params=params)
            r.raise_for_status()
            data = r.json()
        feats = data.get("features") or []
        if not feats:
            return None
        feat = feats[0]
        rings = (feat.get("geometry") or {}).get("rings") or []
        geometry = []
        for ring in rings:
            conv = [[pt[1], pt[0]] for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
            if len(conv) >= 3:
                geometry.append(conv)
        c = feat.get("centroid") or {}
        lat, lng = c.get("y"), c.get("x")
        if (lat is None or lng is None) and geometry:
            r0 = geometry[0]
            lat = sum(p[0] for p in r0) / len(r0)
            lng = sum(p[1] for p in r0) / len(r0)
        if lat is None or lng is None:
            return None
        return {"geometry": geometry, "lat": lat, "lng": lng}
    except Exception as ex:
        print(f"[parcel_fl] geometry-by-id error: {ex}")
        return None

# FL DOR county codes: alphabetical, starting at 11 (confirmed against live layer)
_CO_NO_TO_COUNTY: dict[int, str] = {
    11: "Alachua",    12: "Baker",       13: "Bay",          14: "Bradford",
    15: "Brevard",    16: "Broward",     17: "Calhoun",      18: "Charlotte",
    19: "Citrus",     20: "Clay",        21: "Collier",      22: "Columbia",
    23: "Miami-Dade", 24: "DeSoto",      25: "Dixie",        26: "Duval",
    27: "Escambia",   28: "Flagler",     29: "Franklin",     30: "Gadsden",
    31: "Gilchrist",  32: "Glades",      33: "Gulf",         34: "Hamilton",
    35: "Hardee",     36: "Hendry",      37: "Hernando",     38: "Highlands",
    39: "Hillsborough", 40: "Holmes",    41: "Indian River", 42: "Jackson",
    43: "Jefferson",  44: "Lafayette",   45: "Lake",         46: "Lee",
    47: "Leon",       48: "Levy",        49: "Liberty",      50: "Madison",
    51: "Manatee",    52: "Marion",      53: "Martin",       54: "Monroe",
    55: "Nassau",     56: "Okaloosa",    57: "Okeechobee",   58: "Orange",
    59: "Osceola",    60: "Palm Beach",  61: "Pasco",        62: "Pinellas",
    63: "Polk",       64: "Putnam",      65: "St. Johns",    66: "St. Lucie",
    67: "Santa Rosa", 68: "Sarasota",    69: "Seminole",     70: "Sumter",
    71: "Suwannee",   72: "Taylor",      73: "Union",        74: "Volusia",
    75: "Wakulla",    76: "Walton",      77: "Washington",
}


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
            "outFields": ("CO_NO,PARCEL_ID,OWN_NAME,OWN_ADDR1,OWN_ADDR2,OWN_CITY,OWN_STATE,OWN_ZIPCD,"
                          "PHY_ADDR1,PHY_CITY,PHY_ZIPCD,DOR_UC,JV,AV_NSD,TV_NSD,LND_VAL,NO_BULDNG,"
                          "SALE_PRC1,SALE_YR1,SALE_PRC2,SALE_YR2,ACT_YR_BLT,TOT_LVG_AR,LND_SQFOOT"),
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

        co_no = attrs.get("CO_NO")
        county = _CO_NO_TO_COUNTY.get(int(co_no)) if co_no is not None else None

        # DOR truncates multi-owner names, leaving a dangling "SMITH JOHN &" —
        # the layer has no OWN_NAME2 field (schema verified), so clean it up
        owner = (attrs.get("OWN_NAME") or "").strip()
        if owner.endswith("&"):
            owner = owner.rstrip("& ").strip() + " (co-owned)"

        def _addr_line(*parts):
            vals = [str(p).strip() for p in parts if p not in (None, "", 0) and str(p).strip()]
            return ", ".join(vals) if vals else None

        return {
            "found": True,
            "county": county,
            "parcel_id": attrs.get("PARCEL_ID"),
            "owner": owner or None,
            "owner_mailing_address": _addr_line(
                attrs.get("OWN_ADDR1"), attrs.get("OWN_ADDR2"), attrs.get("OWN_CITY"),
                attrs.get("OWN_STATE"), attrs.get("OWN_ZIPCD")),
            "county_address_on_file": _addr_line(
                attrs.get("PHY_ADDR1"), attrs.get("PHY_CITY"), attrs.get("PHY_ZIPCD")),
            "acreage": acreage,
            "land_use_code": str(attrs.get("DOR_UC")) if attrs.get("DOR_UC") is not None else None,
            "just_value": attrs.get("JV"),
            "assessed_value_nsd": attrs.get("AV_NSD"),
            "taxable_value_nsd": attrs.get("TV_NSD"),
            "land_value": attrs.get("LND_VAL"),
            "building_count": attrs.get("NO_BULDNG") or 0,
            "year_built": attrs.get("ACT_YR_BLT") or None,
            "living_area_sqft": attrs.get("TOT_LVG_AR") or None,
            "last_sale_price": attrs.get("SALE_PRC1"),
            "last_sale_year": attrs.get("SALE_YR1"),
            "prior_sale_price": attrs.get("SALE_PRC2"),
            "prior_sale_year": attrs.get("SALE_YR2"),
            "geometry": geometry_rings,
            "source": "FL DOR Cadastral",
        }
    except Exception as e:
        print(f"[ParcelFL] Error: {e}")
        return DEFAULT
