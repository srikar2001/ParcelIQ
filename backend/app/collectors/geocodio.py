import os
import httpx


async def geocode(address: str) -> dict | None:
    try:
        api_key = os.environ['GEOCODIO_API_KEY']
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.geocod.io/v1.7/geocode",
                params={"q": address, "api_key": api_key, "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return None
            loc = results[0]["location"]
            return {
                "lat": loc["lat"],
                "lng": loc["lng"],
                "formatted_address": results[0]["formatted_address"],
            }
    except Exception as e:
        print(f"[Geocodio] Error: {e}")
        return None


async def geocode_batch(addresses: list[str]) -> dict[str, dict]:
    """Geocode up to 10,000 addresses in a single API call.
    Returns {input_address: {lat, lng, formatted_address}} for successful ones.
    """
    if not addresses:
        return {}
    try:
        api_key = os.environ['GEOCODIO_API_KEY']
        # Geocodio batch: POST list of strings, returns results in same order
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.geocod.io/v1.7/geocode",
                params={"api_key": api_key},
                json=addresses,
            )
            resp.raise_for_status()
            data = resp.json()

        out: dict[str, dict] = {}
        for item in data.get("results", []):
            query = item.get("query", "")
            hits  = item.get("response", {}).get("results", [])
            if hits:
                loc = hits[0]["location"]
                out[query] = {
                    "lat": loc["lat"],
                    "lng": loc["lng"],
                    "formatted_address": hits[0]["formatted_address"],
                }
        return out
    except Exception as e:
        print(f"[Geocodio Batch] Error: {e}")
        return {}
