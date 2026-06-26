import httpx

DEFAULT = {'easement_found': False, 'easement_type': None, 'source': 'USDA NRCS / FNAI'}
EASEMENT_URL = ('https://services.arcgis.com/9Jk4Zl9KofTtvg3x/arcgis/rest/'
                'services/Conservation_Easements_web/FeatureServer/10/query')


async def get_conservation_easement(lat: float, lng: float) -> dict:
    try:
        params = {
            'where': '1=1', 'geometry': f'{lng},{lat}',
            'geometryType': 'esriGeometryPoint', 'inSR': '4326',
            'spatialRel': 'esriSpatialRelIntersects',
            'outFields': 'MANAME,OWNER,ESMT_HOLD,COUNTY',
            'returnGeometry': 'false', 'f': 'json',
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(EASEMENT_URL, params=params)
            data = resp.json()
        features = data.get('features', [])
        if not features:
            return DEFAULT
        attrs = features[0].get('attributes', {})
        program = (attrs.get('MANAME') or attrs.get('ESMT_HOLD') or 'Conservation easement')
        return {'easement_found': True, 'easement_type': str(program),
                'source': 'USDA NRCS / FNAI'}
    except Exception as e:
        print(f'[Easement] Error: {e}')
        return DEFAULT
