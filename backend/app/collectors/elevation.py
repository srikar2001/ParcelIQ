import httpx

DEFAULT = {'elevation_ft': None, 'source': 'USGS National Elevation Dataset'}


async def get_elevation(lat: float, lng: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                'https://epqs.nationalmap.gov/v1/json',
                params={'x': lng, 'y': lat, 'wkid': 4326,
                        'includeDate': False, 'units': 'Feet'}
            )
            data = resp.json()
        elev = data.get('value')
        # (old fallback endpoint nationalmap.gov/epqs/pqs.php is decommissioned —
        # transient failures are retried by the batch orchestrator instead)
        if elev is None or float(elev) < -900:
            return DEFAULT
        return {'elevation_ft': round(float(elev), 1),
                'source': 'USGS National Elevation Dataset'}
    except Exception as e:
        print(f'[Elevation] Error: {e}')
        return DEFAULT
