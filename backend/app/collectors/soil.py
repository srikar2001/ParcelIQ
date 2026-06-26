import httpx

DEFAULT = {'soil_drainage': None, 'soil_name': None,
           'septic_suitable': None, 'source': 'USDA Web Soil Survey'}
POOR = {'very poorly drained', 'poorly drained', 'somewhat poorly drained'}
GOOD = {'well drained', 'excessively drained', 'somewhat excessively drained'}


async def get_soil_data(lat: float, lng: float) -> dict:
    try:
        query = (
            "SELECT mapunit.muname, component.drainagecl "
            "FROM mapunit "
            "INNER JOIN component ON mapunit.mukey = component.mukey "
            "WHERE component.majcompflag = 'Yes' "
            "AND mapunit.mukey IN ("
            f"SELECT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84("
            f"'POINT({lng} {lat})')"
            ")"
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                'https://sdmdataaccess.nrcs.usda.gov/tabular/post.rest',
                data={'query': query, 'format': 'json+columnname'}
            )
            data = resp.json()
        table = data.get('Table', [])
        if len(table) < 2:
            return DEFAULT
        row = table[1]
        if len(row) < 2:
            return DEFAULT
        soil_name = str(row[0] or '').strip()
        drainage = str(row[1] or '').lower().strip()
        septic_ok = True if drainage in GOOD else (False if drainage in POOR else None)
        return {'soil_drainage': drainage, 'soil_name': soil_name,
                'septic_suitable': septic_ok, 'source': 'USDA Web Soil Survey'}
    except Exception as e:
        print(f'[Soil] Error: {e}')
        return DEFAULT
