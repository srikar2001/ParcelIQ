# ParcelIQ Backlog

Status: TODO · IN PROGRESS (agent, date) · DONE (date, commit) · BLOCKED (why)
Chief-of-staff keeps each lane ordered by impact. Agents add findings here.

## Screening (screening-engineer)
- [ ] TODO S1 Narrative model: `core/narrative.py` uses retired `gemini-1.5-flash`, so it likely always falls back to paid Claude. Move to a current free Gemini model; verify output.
- [ ] TODO S2 Measure per-collector success rate + latency on a 20-parcel batch (direct `_screen_coordinate` calls); fix the worst failure/timeout (audit_report.md §3-4: timeout inversion, dead USGS EPQS fallback).
- [ ] TODO S3 Geocode accuracy: confirm low-accuracy matches (place/street-level) are flagged to the user, never silently scored (audit §3.3, §5).
- [ ] TODO S4 When `/health` shows geostore available=true on Railway, re-run `scripts/validate_geostore.py` against prod numbers and log it.

## Land Leads (leads-engineer)
- [ ] TODO L1 Quality audit: run `/api/leads/search` logic directly for 10 counties × land types; score yield vs requested, KILL leakage, junk/duplicate addresses, variety between runs. Fix the worst.
- [ ] TODO L2 Background top-up of `lead_inventory` for warm counties (the "v2" in memory project_land_leads_v3).
- [ ] TODO L3 Pull owner name line 2 + owner mailing address (OWN_NAME2, OWN_ADDR1/CITY/STATE/ZIPCD) into parcel_info, leads table and exports.

## Build (product-builder)
- [ ] IN PROGRESS (product-builder, 2026-10-19) B1 3D land view: free 3D terrain + satellite toggle on the Deal Review map and Land Leads map (e.g. MapLibre GL JS + free terrain-RGB tiles such as AWS Terrain Tiles "terrarium", Esri World Imagery with attribution). Parcel outline draped on terrain, tilt/rotate, works on mobile.
- [ ] TODO B2 Lead Hunter: saved "buy box" searches that re-run nightly and surface NEW matching PURSUE lots in-app (badge + list). Email later (needs a free provider — owner decision).
- [ ] TODO B3 Deal Analyst: per-parcel Q&A in Deal Review grounded ONLY in that parcel's screening data, with sources; per-user daily cap.
- [ ] TODO B4 Offer helper: suggested offer range from nearby sales (`/api/explore/comps`) + downloadable offer letter (needs L3 mailing address).
- [ ] TODO B5 Due-diligence checklist per county for PURSUE lots (who to call for zoning/permits/septic, what to ask).
- Ideas inbox (any agent may add; builder/chief promote): 

## QA (qa-bugfixer)
- [ ] IN PROGRESS (qa-bugfixer, 2026-10-19) Q1 Golden set: snapshot ~40 diverse FL coordinates' current verdict+score into `tests/golden.json`; each run compare and investigate any drift.
- [ ] TODO Q2 Logged-out live sweep: landing, signup/login pages, shared links, mobile; console errors; broken links.
- [ ] TODO Q3 Check frontend/index.html == index.html on every run.

## UX (ux-polisher) — rotate through, one per run
- [ ] TODO U1 Landing page · U2 Auth screens · U3 Dashboard · U4 Results table · U5 Deal Review · U6 Land Leads · U7 Settings/Admin · U8 Mobile pass

## Growth (growth-analyst)
- [ ] TODO G1 Weekly aggregate report → `ops/reports/YYYY-MM-DD.md`: signups, activation (signed up → ran a batch), batches, leads searches, retention. Counts only, no personal data.
- [ ] TODO G2 SEO: meta description, Open Graph/Twitter tags + og:image with the logo, sitemap.xml, robots.txt.
- [ ] TODO G3 County landing pages ("Vacant land due diligence in <County>, FL") generated from real data, linked from the landing page.
- [ ] TODO G4 Marketing kit in `ops/marketing/`: positioning, landing copy tests, a launch video draft (brag/hyperframes skill), social post drafts. Drafts only — owner publishes.

## Blocked on owner (agents: don't attempt — remind in digest)
- [ ] Railway API service: add Volume at /data + GEOSTORE_PATH / GEOSTORE_URL / GEOSTORE_SHA256 / GEOSTORE_TOKEN (see memory project_geostore_supabase_free). Then approve dropping fl_* tables and downgrade Supabase to Free.
- [ ] Revoke the GitHub token embedded in the git remote URL; switch to a credential helper.
- [ ] Turn off the idle Railway worker service (skeleton, does no work).
- [ ] Connect Sentry / Supabase / Railway connectors in Claude.
- [ ] Provide Florida test address list with expected verdicts.
