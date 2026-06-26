import httpx

DEFAULT = {'evac_zone': None, 'evac_risk': None,
           'source': 'FL Division of Emergency Management'}

_EVAC_URL = ('https://services1.arcgis.com/CY1LXxl9zlJeBuRZ/ArcGIS/rest/'
             'services/2024_Evacuation_Zones/FeatureServer/0/query')


def _zone_to_risk(zone: str) -> str:
    z = zone.upper().strip()
    if z in ('A',):
        return 'very high'
    if z in ('B',):
        return 'high'
    if z in ('C',):
        return 'medium'
    if z in ('D', 'E', 'F'):
        return 'low'
    return 'low'


async def get_evacuation_zone(lat: float, lng: float) -> dict:
    try:
        params = {
            'where': '1=1',
            'geometry': f'{lng},{lat}',
            'geometryType': 'esriGeometryPoint',
            'inSR': '4326',
            'spatialRel': 'esriSpatialRelIntersects',
            'outFields': 'EZone,COUNTY_ZON,County_Nam',
            'returnGeometry': 'false',
            'f': 'json',
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(_EVAC_URL, params=params)
            data = resp.json()
        features = data.get('features', [])
        if not features:
            return {'evac_zone': None, 'evac_risk': None,
                    'source': 'FL Division of Emergency Management'}
        attrs = features[0].get('attributes', {})
        zone = str(attrs.get('EZone') or '').strip().upper()
        county = str(attrs.get('County_Nam') or '').strip().title()
        if not zone:
            return DEFAULT
        return {
            'evac_zone': zone,
            'evac_county_zone': attrs.get('COUNTY_ZON', ''),
            'evac_county': county,
            'evac_risk': _zone_to_risk(zone),
            'source': 'FL Division of Emergency Management',
        }
    except Exception as e:
        print(f'[Evacuation] Error: {e}')
        return DEFAULT
