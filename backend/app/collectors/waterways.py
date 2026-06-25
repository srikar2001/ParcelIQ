import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "ParcelIQ/1.0"}
DEFAULT = {"waterway_nearby": False, "waterway_type": None, "distance_approx": None, "source": "OpenStreetMap"}


async def get_waterway_proximity(lat: float, lng: float) -> dict:
    try:
        query = f"""[out:json];
(
  way(around:200,{lat},{lng})[waterway];
  relation(around:200,{lat},{lng})[waterway];
);
out tags;"""
        async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
            resp = await client.post(OVERPASS_URL, data={"data": query})
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
        if not elements:
            return DEFAULT
        waterway_types = [el.get("tags", {}).get("waterway", "") for el in elements]
        priority = ["canal", "river", "stream", "drain", "ditch"]
        best_type = "waterway"
        for p in priority:
            if p in waterway_types:
                best_type = p
                break
        return {
            "waterway_nearby": True,
            "waterway_type": best_type,
            "distance_approx": "< 200m",
            "source": "OpenStreetMap",
        }
    except Exception as e:
        print(f"[Waterways] Error: {e}")
        return DEFAULT
