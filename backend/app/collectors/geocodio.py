import os
import httpx


async def geocode(address: str) -> dict | None:
    try:
        api_key = os.environ['GEOCODIO_API_KEY']
        async with httpx.AsyncClient(timeout=10.0) as client:
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
