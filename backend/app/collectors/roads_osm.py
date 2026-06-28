import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "ParcelIQ/1.0", "Accept": "*/*"}
DEFAULT = {"road_found": False, "road_surface": "error", "road_type": None, "source": "OpenStreetMap"}

PAVED = {"paved", "asphalt", "concrete", "cobblestone"}
DIRT  = {"unpaved", "dirt", "gravel", "ground", "grass", "sand", "compacted"}


async def get_road_type(lat: float, lng: float) -> dict:
    try:
        query = f"[out:json];way(around:100,{lat},{lng})[highway];out tags;"
        async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
            resp = await client.post(OVERPASS_URL, data={"data": query})
            resp.raise_for_status()
            elements = resp.json().get("elements", [])

        if not elements:
            return DEFAULT

        # Prefer classified roads over tracks
        best = elements[0]
        for el in elements:
            hw = el.get("tags", {}).get("highway", "")
            if hw in ("residential", "primary", "secondary", "tertiary", "unclassified"):
                best = el
                break

        tags = best.get("tags", {})
        highway = tags.get("highway")
        surface = tags.get("surface", "").lower()

        if surface in PAVED:
            road_surface = "paved"
        elif surface in DIRT:
            road_surface = "dirt"
        else:
            road_surface = "unknown"

        return {
            "road_found": True,
            "road_surface": road_surface,
            "road_type": highway,
            "source": "OpenStreetMap",
        }
    except Exception as e:
        print(f"[RoadsOSM] Error: {e}")
        return DEFAULT
