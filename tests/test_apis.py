"""
Test all 12 Hillsborough County GIS API endpoints.
Uses Angel Ln centroid: lat=28.1728, lon=-82.5538, folio=8.0000
Run: python tests/test_apis.py
"""
import asyncio
import sys
import httpx

LAT = 28.1728
LON = -82.5538
FOLIO = "8.0000"
ADDR = "19931 ANGEL LN"
TIMEOUT = 20.0

POINT_PARAMS = {
    "geometry": f"{LON},{LAT}",
    "geometryType": "esriGeometryPoint",
    "inSR": "4326",
    "spatialRel": "esriSpatialRelIntersects",
    "outFields": "*",
    "f": "json",
}

SOURCES = [
    {
        "id": "01",
        "name": "HCPA Parcel",
        "url": "https://gis.tpcmaps.org/arcgis/rest/services/Parcels/MapServer/2/query",
        "params": {"where": f"FOLIO = '{FOLIO}'", "outFields": "FOLIO,OWNER,SITE_ADDR,PAR_ACREAGE", "f": "json"},
        "check": lambda d: bool(d.get("features")),
        "detail": lambda d: f"FOLIO={d['features'][0]['attributes'].get('FOLIO')}, OWNER={d['features'][0]['attributes'].get('OWNER', '')[:20]}..." if d.get("features") else "no features",
    },
    {
        "id": "02",
        "name": "FEMA Flood (NFHL)",
        "url": "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",
        "params": POINT_PARAMS,
        "check": lambda d: "features" in d,
        "detail": lambda d: f"FLD_ZONE={d['features'][0]['attributes'].get('FLD_ZONE')}" if d.get("features") else "no features (Zone X expected at this location)",
    },
    {
        "id": "03",
        "name": "NWI Wetlands",
        "url": "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Wetlands/FeatureServer/0/query",
        "params": POINT_PARAMS,
        "check": lambda d: "features" in d,
        "detail": lambda d: f"found={len(d.get('features', []))} wetland polygons" if "features" in d else "error",
    },
    {
        "id": "04",
        "name": "HCPA Geometry",
        "url": "https://gis.tpcmaps.org/arcgis/rest/services/Parcels/MapServer/2/query",
        "params": {"where": f"FOLIO = '{FOLIO}'", "returnGeometry": "true", "outSR": "4326", "outFields": "FOLIO", "f": "json"},
        "check": lambda d: bool(d.get("features") and d["features"][0].get("geometry")),
        "detail": lambda d: f"rings={len(d['features'][0]['geometry'].get('rings', []))} polygon rings" if d.get("features") else "no geometry",
    },
    {
        "id": "05",
        "name": "Zoning",
        "url": "https://gisdextweb1.hillsboroughcounty.org/arcgis/rest/services/Hosted/Zoning_and_Regulatory/FeatureServer/0/query",
        "params": POINT_PARAMS,
        "check": lambda d: "features" in d,
        "detail": lambda d: f"nzone={d['features'][0]['attributes'].get('nzone')}" if d.get("features") else "no features",
    },
    {
        "id": "06",
        "name": "Future Land Use",
        "url": "https://gis.tpcmaps.org/arcgis/rest/services/LandUse/FutureLU_HC/MapServer/2/query",
        "params": POINT_PARAMS,
        "check": lambda d: "features" in d,
        "detail": lambda d: f"FLUE={d['features'][0]['attributes'].get('FLUE')}" if d.get("features") else "no features",
    },
    {
        "id": "07A",
        "name": "Water Service Area",
        "url": "https://gisdextweb1.hillsboroughcounty.org/arcgis/rest/services/Hosted/Utility_Service_Area/FeatureServer/0/query",
        "params": POINT_PARAMS,
        "check": lambda d: "features" in d,
        "detail": lambda d: f"found={len(d.get('features', []))} water polygons",
    },
    {
        "id": "07B",
        "name": "Sewer Service Area",
        "url": "https://gisdextweb1.hillsboroughcounty.org/arcgis/rest/services/Hosted/Utility_Service_Area/FeatureServer/1/query",
        "params": POINT_PARAMS,
        "check": lambda d: "features" in d,
        "detail": lambda d: f"found={len(d.get('features', []))} sewer polygons",
    },
    {
        "id": "08",
        "name": "Road Authority",
        "url": "https://gisdextweb1.hillsboroughcounty.org/arcgis/rest/services/Hosted/Roads_and_Transportation/FeatureServer/1/query",
        "params": {"where": "UPPER(street) LIKE '%ANGEL%'", "outFields": "street,authrty,maintby,maintainedby", "f": "json"},
        "check": lambda d: "features" in d,
        "detail": lambda d: f"found={len(d.get('features', []))} roads | name={d['features'][0]['attributes'].get('street')}" if d.get("features") else "no records",
    },
    {
        "id": "09",
        "name": "Permits",
        "url": "https://maps.hillsboroughcounty.org/arcgis/rest/services/PermitsPlus/ResidentialCommericalIssuedPermitsCertOccMapService/FeatureServer/0/query",
        "params": POINT_PARAMS,
        "check": lambda d: "features" in d,
        "detail": lambda d: f"found={len(d.get('features', []))} permit records",
    },
    {
        "id": "10",
        "name": "Evac Zone (HEAT 2026)",
        "url": "https://gisdextweb1.hillsboroughcounty.org/arcgis/rest/services/Hosted/HEAT_2026_Webmap/FeatureServer/1/query",
        "params": POINT_PARAMS,
        "check": lambda d: "features" in d,
        "detail": lambda d: f"found={len(d.get('features', []))} evac zones",
    },
    {
        "id": "12",
        "name": "Address Points",
        "url": "https://maps.hillsboroughcounty.org/arcgis/rest/services/DSD_Viewer_Services/DSD_Viewer_Address_Points_Numbers_Only/MapServer/0/query",
        "params": {"where": "ADDRNUM = '19931'", "outFields": "FULLADDR,FOLIO,ZIP", "f": "json"},
        "check": lambda d: bool(d.get("features")),
        "detail": lambda d: f"FULLADDR={d['features'][0]['attributes'].get('FULLADDR')}" if d.get("features") else "no features",
    },
]


async def test_source(client: httpx.AsyncClient, src: dict) -> tuple[bool, str, str]:
    try:
        resp = await client.get(src["url"], params=src["params"], timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            return False, src["name"], f"API error: {data['error']}"

        ok = src["check"](data)
        detail = src["detail"](data)
        return ok, src["name"], detail
    except Exception as exc:
        return False, src["name"], str(exc)[:80]


async def main():
    print(f"\nParcelIQ — 12 API Source Tests")
    print(f"Test parcel: {ADDR} | Folio {FOLIO} | {LAT}, {LON}")
    print("─" * 65)
    print(f"{'ID':<5} {'API SOURCE':<25} {'STATUS':<8} {'DETAIL'}")
    print("─" * 65)

    passed = 0
    failed = 0

    async with httpx.AsyncClient() as client:
        # Small stagger to avoid concurrent hammering of same host
        tasks = []
        for i, src in enumerate(SOURCES):
            await asyncio.sleep(0.05 * i)
            tasks.append(asyncio.create_task(test_source(client, src)))
        results = await asyncio.gather(*tasks)

    PASS_STR = "\033[92mPASS\033[0m"
    FAIL_STR = "\033[91mFAIL\033[0m"

    for (ok, name, detail), src in zip(results, SOURCES):
        status = PASS_STR if ok else FAIL_STR
        sid = src["id"]
        detail_trunc = detail[:45] if len(detail) > 45 else detail
        print(f"  {sid:<5} {name:<25} {status}   {detail_trunc}")
        if ok:
            passed += 1
        else:
            failed += 1

    print("─" * 65)
    print(f"\n  Results: {passed}/12 passed, {failed} failed\n")
    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
