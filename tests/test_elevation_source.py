"""Offline test: elevation source choice (S14).

Open-Meteo (90 m surface model) is trusted only on high ground; low readings are
re-measured with USGS 3DEP lidar (EPQS), with Open-Meteo as the fallback.

  backend/venv/bin/python tests/test_elevation_source.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.collectors import elevation as E  # noqa: E402


def run(om, ep):
    calls = []

    async def fake_om(lat, lng):
        calls.append('om')
        if isinstance(om, Exception):
            raise om
        return om

    async def fake_ep(lat, lng):
        calls.append('ep')
        if isinstance(ep, Exception):
            raise ep
        return ep

    E._open_meteo, E._epqs, E._open_meteo_paused_until = fake_om, fake_ep, 0.0
    return asyncio.run(E.get_elevation(25.79, -80.13)), calls


def main():
    r, calls = run(187.0, 146.0)           # high ground: no slow EPQS call
    assert r == {'elevation_ft': 187.0, 'source': 'Open-Meteo (Copernicus DEM)'} and calls == ['om'], (r, calls)
    r, calls = run(19.7, 5.8)              # Miami Beach: lidar wins
    assert r == {'elevation_ft': 5.8, 'source': 'USGS 3DEP (EPQS)'} and calls == ['om', 'ep'], (r, calls)
    r, _ = run(9.8, TimeoutError())        # EPQS down: fall back to Open-Meteo
    assert r == {'elevation_ft': 9.8, 'source': 'Open-Meteo (Copernicus DEM)'}, r
    r, _ = run(0.0, None)                  # open water: EPQS "Call failed" → Open-Meteo
    assert r['elevation_ft'] == 0.0 and r['source'].startswith('Open-Meteo'), r
    r, _ = run(None, 3.0)                  # Open-Meteo rate-limited
    assert r == {'elevation_ft': 3.0, 'source': 'USGS 3DEP (EPQS)'}, r
    r, _ = run(ValueError(), None)         # both fail
    assert r['elevation_ft'] is None, r
    print('elevation source: 6/6 ok')


if __name__ == '__main__':
    main()
