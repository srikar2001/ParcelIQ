import asyncio

from app.core.http import pooled_client

# EPA retired the ECHO "Superfund_Sites_FRS" MapServer (echodata.epa.gov now
# 404s), which made this check silently report "no contamination" for every
# parcel. EPA publishes the same Superfund (SEMS) inventory on its ArcGIS Online
# org: FRS site points (NPL + non-NPL, ~600 in FL) and the mapped NPL site
# boundaries, so a parcel inside a large site far from its point still hits.
#
# ~38% of FL SEMS points have no recorded collection method and some are
# placeholders (e.g. "USN NAVAL AIR STA MAYPORT" sits in downtown Jacksonville,
# three Bartow sites share one coordinate). Those are returned flagged
# `approximate` and do NOT set contamination_found (the -25 deduction); the
# scorer surfaces them as a verify-manually note instead.
_BASE = "https://services.arcgis.com/cJ9YHowT8TU7DUyn/ArcGIS/rest/services"
POINTS_URL = f"{_BASE}/FRS_INTERESTS_SEMS/FeatureServer/0/query"
BOUNDS_URL = f"{_BASE}/FAC_Superfund_Site_Boundaries_EPA_Public/FeatureServer/0/query"
SOURCE = "EPA Superfund (SEMS)"

DEFAULT = {"contamination_found": False, "sites": [], "source": SOURCE}
# Lookup failed: NOT the same as clean — scoring reports it as a data gap.
ERROR = {"contamination_found": False, "sites": [], "error": True, "source": SOURCE}


async def _query(client, url: str, lat: float, lng: float, distance_m: int, fields: str) -> list:
    params = {
        "where": "1=1",
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": fields,
        "returnGeometry": "false",
        "f": "json",
    }
    if distance_m:
        params.update(distance=str(distance_m), units="esriSRUnit_Meter")
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message") or "ArcGIS error")
    return [f.get("attributes", {}) for f in data.get("features", [])]


async def get_epa_superfund(lat: float, lng: float) -> dict:
    try:
        async with pooled_client(timeout=15.0) as client:
            points, bounds = await asyncio.gather(
                _query(client, POINTS_URL, lat, lng, 500,
                       "PRIMARY_NAME,INTEREST_TYPE,ACTIVE_STATUS,COLLECT_MTH_DESC"),
                _query(client, BOUNDS_URL, lat, lng, 0,
                       "SITE_NAME,NPL_STATUS_CODE"),
                return_exceptions=True,
            )
    except Exception as e:
        print(f"[EPA] Error: {e!r}")
        return dict(ERROR)

    seen: set[str] = set()
    sites = []

    def add(name, typ, status, approximate=False):
        name = (name or "").strip()
        if name and name.upper() not in seen:
            seen.add(name.upper())
            sites.append({"name": name, "type": typ or "", "status": status or "",
                          "approximate": approximate})

    if isinstance(bounds, list):
        for a in bounds:
            add(a.get("SITE_NAME"), "SUPERFUND NPL (site boundary)", a.get("NPL_STATUS_CODE"))
    if isinstance(points, list):
        for a in points:
            method = (a.get("COLLECT_MTH_DESC") or "").strip().upper()
            add(a.get("PRIMARY_NAME"), a.get("INTEREST_TYPE"), a.get("ACTIVE_STATUS"),
                approximate=method in ("", "UNKNOWN"))

    if sites:
        return {"contamination_found": any(not x["approximate"] for x in sites),
                "sites": sites, "source": SOURCE}
    for r in (points, bounds):
        if isinstance(r, BaseException):
            print(f"[EPA] Error: {r!r}")
            return dict(ERROR)
    return dict(DEFAULT)
