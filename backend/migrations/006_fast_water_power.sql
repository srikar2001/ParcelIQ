-- Migration 006: make waterway_near / power_near fast.
-- The originals used ST_DWithin(geom::geography, ...), and casting every row's
-- geometry to geography skips the GIST index -> a seq scan (1-3s). Add a
-- bbox pre-filter (geom && ST_Expand(pt, ...)) that DOES use the index to
-- prune to a handful of candidates, then refine with the exact geography
-- distance on those few. Same results, ~10x faster. Idempotent.

CREATE OR REPLACE FUNCTION waterway_near(in_lat double precision, in_lng double precision)
RETURNS TABLE(nearby boolean, waterway_type text, near_lat double precision, near_lng double precision)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  ptm geometry  := ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326);
  ptg geography := ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326)::geography;
  bbox geometry := ST_Expand(ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326), 0.0025); -- ~278m
  best text;
  cp   geometry;
BEGIN
  SELECT CASE WHEN w.waterway IN ('canal','river','stream','drain','ditch')
              THEN w.waterway ELSE 'waterway' END
    INTO best
  FROM fl_waterways w
  WHERE w.geom && bbox AND ST_DWithin(w.geom::geography, ptg, 200)
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
  WHERE w.geom && bbox AND ST_DWithin(w.geom::geography, ptg, 200)
  ORDER BY w.geom <-> ptm
  LIMIT 1;

  RETURN QUERY SELECT true, best, ST_Y(cp)::double precision, ST_X(cp)::double precision;
END;
$$;

CREATE OR REPLACE FUNCTION power_near(in_lat double precision, in_lng double precision)
RETURNS TABLE(within_500 boolean, within_1600 boolean, near_lat double precision, near_lng double precision)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  ptm geometry  := ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326);
  ptg geography := ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326)::geography;
  bb500  geometry := ST_Expand(ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326), 0.006);  -- ~667m
  bb1600 geometry := ST_Expand(ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326), 0.018);  -- ~2km
  cp  geometry;
BEGIN
  SELECT ST_ClosestPoint(p.geom, ptm) INTO cp
  FROM fl_powerlines p
  WHERE p.geom && bb500 AND ST_DWithin(p.geom::geography, ptg, 500)
  ORDER BY p.geom <-> ptm
  LIMIT 1;

  IF cp IS NOT NULL THEN
    RETURN QUERY SELECT true, true, ST_Y(cp)::double precision, ST_X(cp)::double precision;
    RETURN;
  END IF;

  PERFORM 1 FROM fl_powerlines p
  WHERE p.geom && bb1600 AND ST_DWithin(p.geom::geography, ptg, 1600) LIMIT 1;
  IF FOUND THEN
    RETURN QUERY SELECT false, true, NULL::double precision, NULL::double precision;
    RETURN;
  END IF;

  RETURN QUERY SELECT false, false, NULL::double precision, NULL::double precision;
END;
$$;

GRANT EXECUTE ON FUNCTION waterway_near(double precision, double precision) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION power_near(double precision, double precision)    TO anon, authenticated, service_role;
