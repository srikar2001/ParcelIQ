#!/usr/bin/env bash
# Phase 2 / Session 2.1 — load filtered Florida OSM roads/waterways/power
# lines into PostGIS (Supabase). Reproducible re-run of the in-house data
# that replaces the per-parcel live Overpass/road-access API calls.
#
# Prerequisites (macOS/Homebrew):
#   brew install gdal libpq        # ogr2ogr + psql
#
# Source data — filtered Florida OSM extract (see Phase 0). Produce with:
#   curl -L -o florida-latest.osm.pbf \
#     https://download.geofabrik.de/north-america/us/florida-latest.osm.pbf
#   osmium tags-filter florida-latest.osm.pbf w/highway            -o florida-roads.osm.pbf
#   osmium tags-filter florida-latest.osm.pbf w/waterway           -o florida-waterways.osm.pbf
#   osmium tags-filter florida-latest.osm.pbf w/power=line,minor_line -o florida-power.osm.pbf
#
# Usage:
#   PGURL='postgresql://postgres:<pw>@db.<ref>.supabase.co:5432/postgres?sslmode=require' \
#   OSM_DIR=/path/to/filtered/pbf/dir \
#   bash backend/scripts/load_osm_florida.sh
#
# Idempotent: each load uses -overwrite, so re-runs replace the tables.
# Source geometry is already EPSG:4326 (WGS84) — no reprojection.
#
# Verified load (2026-07-16, Supabase Pro, 8GB disk):
#   fl_roads       2,833,377 rows   744 MB (561 table + 181 GIST)
#   fl_waterways      49,614 rows    17 MB
#   fl_powerlines     17,540 rows   7.7 MB
set -euo pipefail

OSM_DIR="${OSM_DIR:?set OSM_DIR to the directory holding the filtered .pbf files}"
: "${PGURL:?set PGURL to the Postgres connection string}"

# Custom OSM config exposes power+voltage as columns (not in GDAL's default
# line attributes). Generated at runtime so a stale temp file can't break it.
CONF="$(mktemp -t osmconf.XXXXXX.ini)"
trap 'rm -f "$CONF"' EXIT
DEFAULT_CONF="$(find /opt/homebrew /usr -name osmconf.ini -path '*share/gdal*' 2>/dev/null | head -1)"
[ -n "$DEFAULT_CONF" ] || { echo "ERROR: GDAL default osmconf.ini not found"; exit 1; }
sed 's/^attributes=name,highway,waterway,aerialway,barrier,man_made,railway/attributes=name,highway,waterway,power,voltage,aerialway,barrier,man_made,railway/' \
  "$DEFAULT_CONF" > "$CONF"

echo "=== Enable PostGIS ==="
psql "$PGURL" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS postgis;"

COMMON=(-f PostgreSQL -overwrite -nlt LINESTRING \
        -lco GEOMETRY_NAME=geom -lco SPATIAL_INDEX=GIST -lco FID=id \
        -gt 65536 --config OSM_CONFIG_FILE "$CONF" --config PG_USE_COPY YES)

echo "=== fl_roads ==="
ogr2ogr "${COMMON[@]}" -nln fl_roads      -select "osm_id,name,highway" \
  -where "highway IS NOT NULL"  PG:"$PGURL" "$OSM_DIR/florida-roads.osm.pbf" lines
echo "=== fl_waterways ==="
ogr2ogr "${COMMON[@]}" -nln fl_waterways  -select "osm_id,name,waterway" \
  -where "waterway IS NOT NULL" PG:"$PGURL" "$OSM_DIR/florida-waterways.osm.pbf" lines
echo "=== fl_powerlines ==="
ogr2ogr "${COMMON[@]}" -nln fl_powerlines -select "osm_id,name,power,voltage" \
  -where "power IS NOT NULL"    PG:"$PGURL" "$OSM_DIR/florida-power.osm.pbf" lines

echo "=== Report ==="
psql "$PGURL" -v ON_ERROR_STOP=1 <<'SQL'
SELECT 'fl_roads' AS tbl, count(*) AS rows FROM fl_roads
UNION ALL SELECT 'fl_waterways', count(*) FROM fl_waterways
UNION ALL SELECT 'fl_powerlines', count(*) FROM fl_powerlines ORDER BY tbl;
SELECT relname AS tbl,
  pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
  pg_size_pretty(pg_indexes_size(c.oid))        AS indexes
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND relname IN ('fl_roads','fl_waterways','fl_powerlines')
ORDER BY relname;
SQL
echo "=== DONE ==="
