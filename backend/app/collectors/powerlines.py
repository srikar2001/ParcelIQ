import httpx

OVERPASS = 'https://overpass-api.de/api/interpreter'
HEADERS = {'User-Agent': 'ParcelIQ/1.0'}
DEFAULT = {'powerline_nearby': None, 'powerline_distance': None,
           'source': 'OpenStreetMap'}


async def get_powerline_proximity(lat: float, lng: float) -> dict:
    try:
        q500 = f'''[out:json][timeout:15];
        (way(around:500,{lat},{lng})[power~"^(line|minor_line|cable)$"];
         node(around:500,{lat},{lng})[power~"^(tower|pole)$"];);
        out count;'''
        async with httpx.AsyncClient(timeout=18.0, headers=HEADERS) as client:
            r = await client.post(OVERPASS, data={'data': q500})
            data = r.json()
        count_el = next((e for e in data.get('elements', []) if e.get('type') == 'count'), None)
        if count_el:
            total = int(count_el.get('tags', {}).get('total', 0))
        else:
            total = len([e for e in data.get('elements', []) if e.get('type') in ('way', 'node')])
        if total > 0:
            return {'powerline_nearby': True, 'powerline_distance': '< 500m',
                    'source': 'OpenStreetMap'}
        q1600 = f'''[out:json][timeout:15];
        (way(around:1600,{lat},{lng})[power~"^(line|minor_line)$"];);
        out count;'''
        async with httpx.AsyncClient(timeout=18.0, headers=HEADERS) as client:
            r2 = await client.post(OVERPASS, data={'data': q1600})
            data2 = r2.json()
        count_el2 = next((e for e in data2.get('elements', []) if e.get('type') == 'count'), None)
        if count_el2:
            total2 = int(count_el2.get('tags', {}).get('total', 0))
        else:
            total2 = len([e for e in data2.get('elements', []) if e.get('type') == 'way'])
        if total2 > 0:
            return {'powerline_nearby': True, 'powerline_distance': '< 1 mile',
                    'source': 'OpenStreetMap'}
        return {'powerline_nearby': False, 'powerline_distance': '> 1 mile',
                'source': 'OpenStreetMap'}
    except Exception as e:
        print(f'[Powerlines] Error: {e}')
        return DEFAULT
