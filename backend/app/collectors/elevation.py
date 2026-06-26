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
        if elev is None or float(elev) < -900:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp2 = await client.get(
                    'https://nationalmap.gov/epqs/pqs.php',
                    params={'x': lng, 'y': lat, 'units': 'Feet', 'output': 'json'}
                )
                data2 = resp2.json()
            elev = (data2.get('USGS_Elevation_Point_Query_Service', {})
                        .get('Elevation_Query', {})
                        .get('Elevation'))
        if elev is None or float(elev) < -900:
            return DEFAULT
        return {'elevation_ft': round(float(elev), 1),
                'source': 'USGS National Elevation Dataset'}
    except Exception as e:
        print(f'[Elevation] Error: {e}')
        return DEFAULT
