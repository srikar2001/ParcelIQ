-- Migration 004: local conservation-easement point-in-polygon RPC (Phase 4).
-- Easements are an auto-kill trigger. Replicates easement.py's point-intersect
-- exactly (ST_Intersects on the point), returning found + program name
-- (MANAME, else ESMT_HOLD, else 'Conservation easement'). Idempotent.
CREATE OR REPLACE FUNCTION easement_near(in_lat double precision, in_lng double precision)
RETURNS TABLE(found boolean, program text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  pt geometry := ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326);
  prog text;
BEGIN
  SELECT COALESCE(NULLIF(btrim(e.maname), ''), NULLIF(btrim(e.esmt_hold), ''), 'Conservation easement')
    INTO prog
  FROM fl_easements e
  WHERE ST_Intersects(e.geom, pt)
  LIMIT 1;
  RETURN QUERY SELECT (prog IS NOT NULL), prog;
END;
$$;

GRANT EXECUTE ON FUNCTION easement_near(double precision, double precision) TO anon, authenticated, service_role;
