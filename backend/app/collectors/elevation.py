import httpx

DEFAULT = {'elevation_ft': None, 'source': 'Open-Meteo (Copernicus DEM)'}

# Open-Meteo's elevation API (Copernicus GLO-90 DEM) is free, needs no key, and
# returns in ~0.5s — vs the USGS EPQS service which routinely took 4-6s and was
# the single slowest collector, dominating every parcel's total screen time.
_URL = 'https://api.open-meteo.com/v1/elevation'
_M_TO_FT = 3.28084


async def get_elevation(lat: float, lng: float) -> dict:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(_URL, params={'latitude': lat, 'longitude': lng})
            data = resp.json()
        vals = data.get('elevation')
        elev_m = vals[0] if isinstance(vals, list) and vals else vals
        if elev_m is None:
            return DEFAULT
        return {'elevation_ft': round(float(elev_m) * _M_TO_FT, 1),
                'source': 'Open-Meteo (Copernicus DEM)'}
    except Exception as e:
        print(f'[Elevation] Error: {e}')
        return DEFAULT
