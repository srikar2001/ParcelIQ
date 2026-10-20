import time

from app.core.http import pooled_client

DEFAULT = {'elevation_ft': None, 'source': 'Open-Meteo (Copernicus DEM)'}

# Open-Meteo's elevation API (Copernicus GLO-90 DEM) is free, needs no key, and
# returns in ~0.5s — vs the USGS EPQS service which routinely took 4-6s and was
# the single slowest collector, dominating every parcel's total screen time.
_URL = 'https://api.open-meteo.com/v1/elevation'
_M_TO_FT = 3.28084

# Open-Meteo's free tier is rate limited per IP and every batch on the Railway
# box shares one IP. Over the limit it answers with a JSON error body (no
# 'elevation' key, no exception), so elevation silently went missing and the
# "Very low elevation" kill flag vanished for coastal parcels. USGS EPQS (3DEP,
# free, no key) is the fallback; after a 429 we skip Open-Meteo for a short
# cooldown instead of burning a doomed call per parcel.
_EPQS_URL = 'https://epqs.nationalmap.gov/v1/json'
_COOLDOWN_S = 60.0
_open_meteo_paused_until = 0.0


async def _open_meteo(lat: float, lng: float) -> float | None:
    global _open_meteo_paused_until
    async with pooled_client(timeout=5.0) as client:
        resp = await client.get(_URL, params={'latitude': lat, 'longitude': lng})
    try:
        data = resp.json()
    except ValueError:
        data = {}
    vals = data.get('elevation') if isinstance(data, dict) else None
    elev_m = vals[0] if isinstance(vals, list) and vals else vals
    if elev_m is None:
        if resp.status_code == 429:
            _open_meteo_paused_until = time.monotonic() + _COOLDOWN_S
        reason = data.get('reason') if isinstance(data, dict) else None
        print(f'[Elevation] Open-Meteo HTTP {resp.status_code}: {reason or "no elevation"}')
        return None
    return float(elev_m) * _M_TO_FT


async def _epqs(lat: float, lng: float) -> float | None:
    async with pooled_client(timeout=6.0) as client:
        resp = await client.get(_EPQS_URL, params={
            'x': lng, 'y': lat, 'units': 'Feet', 'wkid': 4326, 'includeDate': 'false'})
    try:
        val = float(resp.json().get('value'))
    except (ValueError, TypeError, AttributeError):
        return None  # off-raster (open water) answers with plain text "Call failed"
    return val if val > -1000 else None  # -1000000 = no data


async def get_elevation(lat: float, lng: float) -> dict:
    if time.monotonic() >= _open_meteo_paused_until:
        try:
            ft = await _open_meteo(lat, lng)
            if ft is not None:
                return {'elevation_ft': round(ft, 1), 'source': 'Open-Meteo (Copernicus DEM)'}
        except Exception as e:
            print(f'[Elevation] Open-Meteo error: {e}')
    try:
        ft = await _epqs(lat, lng)
        if ft is not None:
            return {'elevation_ft': round(ft, 1), 'source': 'USGS 3DEP (EPQS)'}
    except Exception as e:
        print(f'[Elevation] EPQS error: {e}')
    return DEFAULT
