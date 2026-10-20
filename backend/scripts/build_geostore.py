"""
Build data/geostore.sqlite — the Florida reference layers as one local file.

These layers (wetlands, critical habitat, flood zones, easements, waterways,
powerlines) are static public data that used to live in Supabase as PostGIS
tables (~4 GB, the whole reason the project needed the paid plan). The API
now answers the same lookups from this file (app/core/geostore.py), so the
database only has to hold user data.

Layout: one table per layer  (id, a, b, c, d, g)  where a..d are the few
attributes each lookup returns and g is zlib-compressed WKB, plus an SQLite
R-tree  <layer>_idx(id, minx, maxx, miny, maxy)  for the bbox pre-filter.

Usage (needs psql + SUPABASE_DB_URL pointing at a DB that still has fl_*):
    python scripts/build_geostore.py [out_path]
"""
from __future__ import annotations

import csv
import os
import sqlite3
import subprocess
import sys
import time
import zlib
from pathlib import Path

csv.field_size_limit(sys.maxsize)

# layer -> SELECT producing a, b, c, d (any may be NULL)
LAYERS = {
    "wetlands": "wetland_type, attribute, NULL, NULL",
    "habitat": "COALESCE(NULLIF(btrim(comname),''), sciname), NULL, NULL, NULL",
    "flood": "fld_zone, zone_subty, sfha_tf, static_bfe",
    "easements": "COALESCE(NULLIF(btrim(maname),''), NULLIF(btrim(esmt_hold),''), 'Conservation easement'), NULL, NULL, NULL",
    "waterways": "waterway, NULL, NULL, NULL",
    "powerlines": "NULL, NULL, NULL, NULL",
}
SOURCE = {
    "wetlands": "fl_wetlands", "habitat": "fl_habitat", "flood": "fl_flood_zones",
    "easements": "fl_easements", "waterways": "fl_waterways", "powerlines": "fl_powerlines",
}


def export_layer(db: sqlite3.Connection, layer: str, dsn: str) -> int:
    db.execute(f"DROP TABLE IF EXISTS {layer}")
    db.execute(f"DROP TABLE IF EXISTS {layer}_idx")
    db.execute(f"CREATE TABLE {layer}(id INTEGER PRIMARY KEY, a TEXT, b TEXT, c TEXT, d REAL, g BLOB NOT NULL)")
    db.execute(f"CREATE VIRTUAL TABLE {layer}_idx USING rtree(id, minx, maxx, miny, maxy)")
    sql = (
        f"COPY (SELECT {LAYERS[layer]}, ST_XMin(geom), ST_XMax(geom), ST_YMin(geom), ST_YMax(geom), "
        f"encode(ST_AsBinary(geom), 'hex') FROM {SOURCE[layer]} WHERE geom IS NOT NULL) TO STDOUT WITH CSV"
    )
    proc = subprocess.Popen(["psql", dsn, "-X", "-q", "-c", "SET statement_timeout = 0", "-c", sql], stdout=subprocess.PIPE, text=True, bufsize=1 << 20)
    n = 0
    rows, idx = [], []
    for rec in csv.reader(proc.stdout):
        a, b, c, d, x0, x1, y0, y1, hexwkb = rec
        n += 1
        rows.append((n, a or None, b or None, c or None, float(d) if d else None,
                     zlib.compress(bytes.fromhex(hexwkb), 6)))
        idx.append((n, float(x0), float(x1), float(y0), float(y1)))
        if len(rows) >= 20000:
            db.executemany(f"INSERT INTO {layer} VALUES (?,?,?,?,?,?)", rows)
            db.executemany(f"INSERT INTO {layer}_idx VALUES (?,?,?,?,?)", idx)
            rows, idx = [], []
    if rows:
        db.executemany(f"INSERT INTO {layer} VALUES (?,?,?,?,?,?)", rows)
        db.executemany(f"INSERT INTO {layer}_idx VALUES (?,?,?,?,?)", idx)
    if proc.wait() != 0:
        raise RuntimeError(f"psql export of {layer} failed")
    db.commit()
    return n


def main() -> None:
    dsn = os.environ["SUPABASE_DB_URL"]
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "geostore.sqlite")
    tmp = out.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    for layer in LAYERS:
        t = time.time()
        n = export_layer(db, layer, dsn)
        db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"rows_{layer}", str(n)))
        print(f"{layer:11s} {n:>9,} rows  {time.time() - t:6.1f}s", flush=True)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
    db.commit()
    db.execute("VACUUM")
    db.close()
    tmp.replace(out)
    print(f"wrote {out}  {out.stat().st_size / 1e6:,.0f} MB")


if __name__ == "__main__":
    main()
