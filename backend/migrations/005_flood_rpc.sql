-- Migration 005: local flood-zone point-in-polygon RPC (Phase 3, flood).
-- Replaces the live FEMA NFHL call in flood.py with a local PostGIS lookup
-- against fl_flood_zones (all 593k FL flood polygons, generalized ~10m —
-- finer than geocoding error, so point-in-polygon results are unchanged).
-- Returns the containing zone's fields, or no rows if the point is unmapped
-- (collector maps that to NOT_MAPPED). Idempotent.
CREATE OR REPLACE FUNCTION flood_zone_at(in_lat double precision, in_lng double precision)
RETURNS TABLE(fld_zone text, zone_subty text, sfha_tf text, static_bfe double precision)
LANGUAGE sql STABLE AS $$
  SELECT f.fld_zone::text, f.zone_subty::text, f.sfha_tf::text, f.static_bfe
  FROM fl_flood_zones f
  WHERE ST_Intersects(f.geom, ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326))
  -- A point is normally in exactly one zone; on the rare overlap prefer a
  -- named SFHA zone (higher risk) for a safe, deterministic answer.
  ORDER BY (f.fld_zone IS NOT NULL AND f.fld_zone <> '') DESC, (f.sfha_tf = 'T') DESC
  LIMIT 1;
$$;

GRANT EXECUTE ON FUNCTION flood_zone_at(double precision, double precision) TO anon, authenticated, service_role;
