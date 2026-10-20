"""
Offline test for buildable acreage (app/core/buildable.py).

Builds a tiny geostore in a temp dir with one wetland and two flood polygons
over a synthetic square parcel, then checks the clear/wet/flood split.
Run: backend/venv/bin/python tests/test_buildable.py
"""
import os
import sqlite3
import sys
import tempfile
import zlib

from shapely.geometry import box

TMP = tempfile.mkdtemp()
os.environ["GEOSTORE_PATH"] = os.path.join(TMP, "geostore.sqlite")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

LAYERS = ("wetlands", "habitat", "flood", "easements", "waterways", "powerlines")
# ~200 m square parcel near Ocala: lng -82.1000..-82.0980, lat 29.1000..29.1018
P = box(-82.1000, 29.1000, -82.0980, 29.1018)
ROWS = {
    # west quarter is wetland
    "wetlands": [(box(-82.1010, 29.0990, -82.0995, 29.1030), ("Freshwater Forested/Shrub Wetland", "PFO1C", None, None))],
    "flood": [
        # west half is AE (overlaps the wetland quarter) -> flood-only adds a quarter
        (box(-82.1010, 29.0990, -82.0990, 29.1030), ("AE", None, "T", 52.0)),
        # zone X over everything: NOT an SFHA, must be ignored
        (box(-82.1100, 29.0900, -82.0900, 29.1100), ("X", "AREA OF MINIMAL FLOOD HAZARD", "F", None)),
    ],
}


def build():
    c = sqlite3.connect(os.environ["GEOSTORE_PATH"])
    c.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    for layer in LAYERS:
        c.execute(f"CREATE TABLE {layer}(id INTEGER PRIMARY KEY, a TEXT, b TEXT, c TEXT, d REAL, g BLOB NOT NULL)")
        c.execute(f"CREATE VIRTUAL TABLE {layer}_idx USING rtree(id, minx, maxx, miny, maxy)")
        for i, (g, (a, b, cc, d)) in enumerate(ROWS.get(layer, []), 1):
            c.execute(f"INSERT INTO {layer} VALUES (?,?,?,?,?,?)", (i, a, b, cc, d, zlib.compress(g.wkb)))
            x0, y0, x1, y1 = g.bounds
            c.execute(f"INSERT INTO {layer}_idx VALUES (?,?,?,?,?)", (i, x0, x1, y0, y1))
        c.execute("INSERT INTO meta VALUES (?,?)", (f"rows_{layer}", str(len(ROWS.get(layer, [])))))
    c.commit()
    c.close()


def ring(g):
    return [[y, x] for x, y in g.exterior.coords]


def main():
    build()
    from app.core import buildable

    res = buildable.compute_sync([ring(P)])
    assert res["ok"], res
    total = res["mapped_acres"]
    assert 9.0 < total < 11.0, total                     # ~195 m x 200 m ≈ 9.6 ac
    assert abs(res["wetland_pct"] - 25) < 1, res          # west quarter
    assert abs(res["flood_pct"] - 50) < 1, res            # west half, zone X ignored
    assert abs(res["clear_pct"] - 50) < 1, res            # east half is clear
    assert abs(res["flood_only_acres"] - total / 4) < 0.1, res
    assert [z["label"] for z in res["flood_zones"]] == ["AE"], res["flood_zones"]
    assert res["wetland_types"][0]["label"].startswith("Freshwater Forested"), res
    assert abs(res["largest_clear_acres"] - res["clear_acres"]) < 0.01, res
    assert res["overlay"]["wetland"] and res["overlay"]["flood"], "overlay rings missing"

    # A hole (e.g. an out-parcel) in the clear half is subtracted, not added.
    hole = box(-82.0988, 29.1006, -82.0984, 29.1012)
    res2 = buildable.compute_sync([ring(P), ring(hole)])
    assert res2["mapped_acres"] < total and res2["clear_acres"] < res["clear_acres"], res2

    # Bad / missing boundaries degrade to a message, never an exception.
    for bad in ([], [[]], [[[29.1, -82.1], [29.1, -82.1]]], [["x", None]], "nope"):
        r = buildable.compute_sync(bad)
        assert r["ok"] is False and r["reason"] == "no_boundary", (bad, r)

    big = box(-82.2, 29.0, -82.0, 29.2)                  # ~100 sq mi
    assert buildable.compute_sync([ring(big)])["reason"] == "too_large"
    print("buildable: all checks passed", {k: res[k] for k in ("mapped_acres", "clear_acres", "wetland_acres", "flood_acres")})


if __name__ == "__main__":
    main()
