# ParcelIQ Backlog

Status: TODO · IN PROGRESS (agent, date) · DONE (date, commit) · BLOCKED (why)
Chief-of-staff keeps each lane ordered by impact. Agents add findings here.

## Screening (screening-engineer)
- [ ] TODO S8 (from qa) No-parcel on road/water points: FL DOR cadastral is queried with point-intersects only, so a geocode landing on a ROW/centerline/canal gets found=False (no owner/acreage/land use, 11/12 sources). 16/42 golden points (downtown Orlando/Tampa/Tallahassee/Jax, Miami Beach, Key West, Alachua rural…) hit this, yet a ~45 m envelope finds 2-4 parcels at each. Consider nearest-parcel fallback within ~30 m (flag it as 'nearest parcel'). Repro: cadastral query geometry=-81.3792,28.5383 → features:[]; tests/golden.json idx with parcel_id null.
- [ ] TODO S7 (from qa) Security/cost: /api/narrative/{folio}, /api/report/{folio}, /api/autocomplete, /api/suggest and /api/cache/test (writes a cache row per hit) are unauthenticated on prod. Narrative is currently 503 (no GOOGLE_API_KEY/ANTHROPIC_API_KEY on Railway) — before a key is set, require auth or it's an open LLM-spend endpoint.
- [ ] TODO S6 (from qa) EPA ECHO Superfund endpoint returns HTTP 404 (echodata.epa.gov .../Superfund_Sites_FRS/MapServer/0/query) — contamination check is silently dead, and EPA always counts as an OK source.
- [ ] TODO S4 (UNBLOCKED — 2026-10-20 /health: available=true, 2630 MB) When `/health` shows geostore available=true on Railway, re-run `scripts/validate_geostore.py` against prod numbers and log it.
- [ ] TODO S5 (from qa) Outage verdicts: even with flood+wetlands+habitat ALL unavailable a parcel still gets PURSUE (score ~70-96). No REVIEW tier by design, so decide how a parcel with failed fatal-flaw checks should surface (cap score? badge?). Repro: run tests/golden_run.py --only 7 without a geostore (Miami Beach → PURSUE).
- [ ] TODO S1 Narrative model: `core/narrative.py` uses retired `gemini-1.5-flash`, so it likely always falls back to paid Claude. Move to a current free Gemini model; verify output.
- [ ] TODO S2 Measure per-collector success rate + latency on a 20-parcel batch (direct `_screen_coordinate` calls); fix the worst failure/timeout (audit_report.md §3-4: timeout inversion, dead USGS EPQS fallback).
- [ ] TODO S3 Geocode accuracy: confirm low-accuracy matches (place/street-level) are flagged to the user, never silently scored (audit §3.3, §5).

## Land Leads (leads-engineer)
- [ ] TODO L1 Quality audit: run `/api/leads/search` logic directly for 10 counties × land types; score yield vs requested, KILL leakage, junk/duplicate addresses, variety between runs. Fix the worst.
- [ ] TODO L3 Pull owner name line 2 + owner mailing address (OWN_NAME2, OWN_ADDR1/CITY/STATE/ZIPCD) into parcel_info, leads table and exports. Unblocks B4.
- [ ] TODO L2 Background top-up of `lead_inventory` for warm counties (the "v2" in memory project_land_leads_v3).

## Build (product-builder)
- [x] DONE (2026-10-20, merged 4c2513c; agent/product-builder/3d-land-view) B1 3D land view: free 3D terrain + satellite toggle on the Deal Review map and Land Leads map (e.g. MapLibre GL JS + free terrain-RGB tiles such as AWS Terrain Tiles "terrarium", Esri World Imagery with attribution). Parcel outline draped on terrain, tilt/rotate, works on mobile.
- [ ] IN PROGRESS (product-builder, 2026-10-20) B2 Lead Hunter: saved "buy box" searches that re-run nightly and surface NEW matching PURSUE lots in-app (badge + list). Email later (needs a free provider — owner decision).
- [ ] TODO B5 Due-diligence checklist per county for PURSUE lots (who to call for zoning/permits/septic, what to ask).
- [ ] TODO B3 (needs LLM key — after S7+S1 and owner key) Deal Analyst: per-parcel Q&A in Deal Review grounded ONLY in that parcel's screening data, with sources; per-user daily cap.
- [ ] TODO B4 (after L3) Offer helper: suggested offer range from nearby sales (`/api/explore/comps`) + downloadable offer letter (needs L3 mailing address).
- Ideas inbox (any agent may add; builder/chief promote): 

## QA (qa-bugfixer)
- [x] DONE (2026-10-20, merged 4c2513c; agent/qa-bugfixer/golden-set) Q1 Golden set: snapshot ~40 diverse FL coordinates' current verdict+score into `tests/golden.json`; each run compare and investigate any drift.
- [x] DONE (2026-10-20, merged 4c2513c; agent/qa-bugfixer/golden-baseline, stacked on golden-set) Q4 (do first; pairs with S4, same geostore file) Golden baseline needs a LOCAL geostore: Supabase fl_* RPCs (flood_zone_at, wetland_near, habitat_near, easement_near, waterway_near) now 404, so off-Railway every screen runs in outage mode (6-7/12 sources, all 42 points PURSUE). Get geostore.sqlite locally (GitHub release geodata-2026-10-19 or build_geostore.py) at GEOSTORE_PATH, then `tests/golden_run.py --snapshot` and commit tests/golden.json.
- [ ] IN REVIEW (agent/qa-bugfixer/shared-parcel-blank) Q2 Logged-out live sweep: landing, signup/login pages, shared links, mobile; console errors; broken links.
- [ ] TODO Q3 Check frontend/index.html == index.html on every run. (2026-10-19, 2026-10-20 x2: identical)

## UX (ux-polisher) — rotate through, one per run
- [ ] TODO U9 (from qa) `#pricing` is hidden in nav (PRICING_HIDDEN) but still renders "Simple, flat pricing." plans when the hash is typed directly — while beta is free, route #pricing to #landing (or show a beta note). Repro: https://parcel-iq-nine.vercel.app/#pricing logged out.
- [ ] TODO U1 Landing page · U2 Auth screens · U3 Dashboard · U4 Results table · U5 Deal Review · U6 Land Leads · U7 Settings/Admin · U8 Mobile pass

## Growth (growth-analyst)
- [ ] TODO G1 Weekly aggregate report → `ops/reports/YYYY-MM-DD.md`: signups, activation (signed up → ran a batch), batches, leads searches, retention. Counts only, no personal data.
- [ ] TODO G2 SEO: meta description, Open Graph/Twitter tags + og:image with the logo, sitemap.xml, robots.txt.
- [ ] TODO G3 County landing pages ("Vacant land due diligence in <County>, FL") generated from real data, linked from the landing page.
- [ ] TODO G4 Marketing kit in `ops/marketing/`: positioning, landing copy tests, a launch video draft (brag/hyperframes skill), social post drafts. Drafts only — owner publishes.

## Blocked on owner (agents: don't attempt — remind in digest)
- [x] Railway API service Volume at /data + GEOSTORE_* env (DONE — /health available=true, 2026-10-20).
- [ ] Approve dropping Supabase fl_* tables and downgrade Supabase to Free (QA reports the fl_* RPCs already 404 — confirm whether they were dropped).
- [ ] Revoke the GitHub token embedded in the git remote URL; switch to a credential helper.
- [ ] Turn off the idle Railway worker service (skeleton, does no work).
- [ ] Connect Sentry / Supabase / Railway connectors in Claude.
- [ ] Provide Florida test address list with expected verdicts.
- [ ] Narrative (Deal Review AI summary) is dead in prod: /api/narrative returns 503 "set GOOGLE_API_KEY or ANTHROPIC_API_KEY" — add a key on Railway (after S1 + S7).
