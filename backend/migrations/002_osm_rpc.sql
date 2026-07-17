-- Migration 002: spatial RPC functions for waterway + power proximity.
-- Phase 2 (adapted) — replaces the rate-limited Overpass calls in
-- osm_bundle.py with local PostGIS lookups against fl_waterways / fl_powerlines,
-- exposed via PostgREST RPC so the app can call them with the existing anon
-- key (no DB password shipped to Railway). Idempotent (CREATE OR REPLACE).

-- Nearest waterway within 200 m of a point. Mirrors osm_bundle._parse_waterways:
-- nearby = any waterway within 200 m; type = highest-priority type present
-- (canal>river>stream>drain>ditch, else 'waterway'); nearest point for the map line.
CREATE OR REPLACE FUNCTION waterway_near(in_lat double precision, in_lng double precision)
RETURNS TABLE(nearby boolean, waterway_type text, near_lat double precision, near_lng double precision)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  ptm geometry  := ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326);
  ptg geography := ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326)::geography;
  best text;
  cp   geometry;
BEGIN
  SELECT CASE WHEN w.waterway IN ('canal','river','stream','drain','ditch')
              THEN w.waterway ELSE 'waterway' END
    INTO best
  FROM fl_waterways w
  WHERE ST_DWithin(w.geom::geography, ptg, 200)
  ORDER BY CASE w.waterway
             WHEN 'canal' THEN 1 WHEN 'river' THEN 2 WHEN 'stream' THEN 3
             WHEN 'drain' THEN 4 WHEN 'ditch' THEN 5 ELSE 6 END
  LIMIT 1;

  IF best IS NULL THEN
    RETURN QUERY SELECT false, NULL::text, NULL::double precision, NULL::double precision;
    RETURN;
  END IF;

  SELECT ST_ClosestPoint(w.geom, ptm) INTO cp
  FROM fl_waterways w
  WHERE ST_DWithin(w.geom::geography, ptg, 200)
  ORDER BY w.geom <-> ptm
  LIMIT 1;

  RETURN QUERY SELECT true, best, ST_Y(cp)::double precision, ST_X(cp)::double precision;
END;
$$;

-- Power proximity, two-tier. Mirrors osm_bundle.get_osm_bundle:
-- within 500 m -> "< 500m" + nearest point; else within 1600 m -> "< 1 mile"
-- (no point, matching the old count-only query); else "> 1 mile".
-- (Lines-only: fl_powerlines holds power line/minor_line/cable ways. The old
--  500 m tier also counted tower/pole nodes — a marginal, non-kill difference.)
CREATE OR REPLACE FUNCTION power_near(in_lat double precision, in_lng double precision)
RETURNS TABLE(within_500 boolean, within_1600 boolean, near_lat double precision, near_lng double precision)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  ptm geometry  := ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326);
  ptg geography := ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326)::geography;
  cp  geometry;
BEGIN
  SELECT ST_ClosestPoint(p.geom, ptm) INTO cp
  FROM fl_powerlines p
  WHERE ST_DWithin(p.geom::geography, ptg, 500)
  ORDER BY p.geom <-> ptm
  LIMIT 1;

  IF cp IS NOT NULL THEN
    RETURN QUERY SELECT true, true, ST_Y(cp)::double precision, ST_X(cp)::double precision;
    RETURN;
  END IF;

  PERFORM 1 FROM fl_powerlines p WHERE ST_DWithin(p.geom::geography, ptg, 1600) LIMIT 1;
  IF FOUND THEN
    RETURN QUERY SELECT false, true, NULL::double precision, NULL::double precision;
    RETURN;
  END IF;

  RETURN QUERY SELECT false, false, NULL::double precision, NULL::double precision;
END;
$$;

-- Let the anon (publishable) key call these via PostgREST /rest/v1/rpc/*.
GRANT EXECUTE ON FUNCTION waterway_near(double precision, double precision) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION power_near(double precision, double precision)    TO anon, authenticated, service_role;
