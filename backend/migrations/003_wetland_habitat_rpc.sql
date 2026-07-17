-- Migration 003: local wetland + habitat proximity RPCs (Phase 3).
-- Replaces the live USFWS NWI + Critical Habitat API calls with local PostGIS
-- lookups against fl_wetlands / fl_habitat. Uses ST_MakeEnvelope + ST_Intersects
-- to replicate the collectors' exact ESRI-envelope boxes (esriSpatialRelIntersects),
-- so results match the old API and verdicts don't shift. Idempotent.

-- Wetlands: on_parcel = wetland intersecting a ~50m box; nearby = ~111m box.
-- type/code taken from a wetland in the on-parcel box (nearest for determinism),
-- matching wetlands.py (only set when on_parcel).
CREATE OR REPLACE FUNCTION wetland_near(in_lat double precision, in_lng double precision)
RETURNS TABLE(on_parcel boolean, nearby boolean, wetland_type text, wetland_code text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  box_on   geometry := ST_MakeEnvelope(in_lng-0.00045, in_lat-0.00045, in_lng+0.00045, in_lat+0.00045, 4326);
  box_near geometry := ST_MakeEnvelope(in_lng-0.001,   in_lat-0.001,   in_lng+0.001,   in_lat+0.001,   4326);
  pt geometry := ST_SetSRID(ST_MakePoint(in_lng, in_lat), 4326);
  wt text; wc text; has_on boolean; has_near boolean;
BEGIN
  has_on := EXISTS(SELECT 1 FROM fl_wetlands w WHERE ST_Intersects(w.geom, box_on));
  has_near := has_on OR EXISTS(SELECT 1 FROM fl_wetlands w WHERE ST_Intersects(w.geom, box_near));
  IF has_on THEN
    SELECT w.wetland_type, w.attribute INTO wt, wc
    FROM fl_wetlands w
    WHERE ST_Intersects(w.geom, box_on)
    ORDER BY w.geom <-> pt
    LIMIT 1;
  END IF;
  RETURN QUERY SELECT has_on, has_near, wt, wc;
END;
$$;

-- Critical habitat: found = any habitat polygon intersecting a ~333m box;
-- species = distinct common (or scientific) names in that box. Matches
-- critical_habitat.py's envelope + comname/sciname list.
CREATE OR REPLACE FUNCTION habitat_near(in_lat double precision, in_lng double precision)
RETURNS TABLE(found boolean, species text[])
LANGUAGE plpgsql STABLE AS $$
DECLARE
  box geometry := ST_MakeEnvelope(in_lng-0.003, in_lat-0.003, in_lng+0.003, in_lat+0.003, 4326);
BEGIN
  RETURN QUERY
  SELECT EXISTS(SELECT 1 FROM fl_habitat h WHERE ST_Intersects(h.geom, box)),
         COALESCE((SELECT array_agg(DISTINCT COALESCE(NULLIF(btrim(h.comname),''), h.sciname))
                   FROM fl_habitat h
                   WHERE ST_Intersects(h.geom, box)
                     AND COALESCE(NULLIF(btrim(h.comname),''), h.sciname) IS NOT NULL),
                  ARRAY[]::text[]);
END;
$$;

GRANT EXECUTE ON FUNCTION wetland_near(double precision, double precision) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION habitat_near(double precision, double precision)  TO anon, authenticated, service_role;
