# ParcelIQ — Full Pre-Launch Audit
**Date:** 2026-07-05 · **Auditor:** Claude (senior eng review) · **Scope:** entire repo + live app (parcel-iq-nine.vercel.app / parceliq-production-fd20.up.railway.app)

**How this was produced:** every backend file read; frontend (7,333 lines) read in full for all interactive regions; live API exercised with real requests (single search, 10-parcel batch stream, suggest endpoints, garbage/wrong-state geocoding); FL DOR ArcGIS layer queried directly; landing page screenshotted headless at 1440px and 375px.

**Headline finding (live-verified):** on a fresh 10-parcel batch, road data succeeded on **1/10**, powerlines **0/10**, waterways **0/10**, elevation **6/10**, parcel record **7/10** — the batch "completed" in 10 seconds because collectors are fast-failing against Overpass 429s and short timeouts. Separately, **address autocomplete is completely dead in production** (upstream DOR query times out) and **fuzzy geocoding scores the wrong parcel** without warning.

---

## 1. DROPPED FIELDS (fetched from APIs, then discarded before the frontend)

| Where | Field(s) | Impact |
|---|---|---|
| `collectors/geocodio.py` | `accuracy`, `accuracy_type`, `address_components` (returned by Geocodio on every call) | Root cause of all geocoding-accuracy bugs. We can't tell a rooftop match from a town-centroid guess. |
| `collectors/parcel_fl.py` | `PHY_ADDR1`, `PHY_CITY` requested in `outFields` but never returned in the dict | Can't show "address on county file" or detect geocode/parcel mismatch. |
| `collectors/critical_habitat.py` | `status` (endangered vs threatened), `sciname` fetched, only `comname` kept | Severity of the wildlife kill is hidden from the user. |
| `collectors/easement.py` | `OWNER`, `COUNTY` fetched, discarded | Easement holder shown, owner/county context lost. |
| `collectors/roads_osm.py` | OSM `name`, `access`, `tiger:*` tags come back in `out tags;` response | Road name ("access via NW 60th Ave") and `access=private` (a real risk signal) discarded. |
| `routers/batch.py` `/search/parcel` APN path | Fetches `OWN_NAME, JV, LND_VAL, NO_BULDNG, SALE_PRC1…` then uses only the address string, re-fetches everything again | Wasted round-trip; APN search is 2× slower than needed. |
| `routers/search.py` `/api/suggest` | Whole endpoint returns folio/strap/lat/lon — **no frontend code calls it** | Dead endpoint (Hillsborough-only, wrong product scope). |

Note: earlier-reported drops (county, `STATIC_BFE`, `ZONE_SUBTY`, `COUNTY_ZON`, EPA site objects, `parcel_id`, `building_count`, `last_sale_year`) **have already been fixed** in the current code — they flow through `parcel_info`. The remaining gap is the frontend never *displays* most of them (see §6).

## 2. MISSING FIELDS (source API has them; we never request)

- **FL DOR Cadastral** (biggest wins, all on the same layer we already query):
  - `OWN_NAME2` — second owner line. Its absence is why owners render truncated with a trailing "&" (e.g. "SMITH JOHN &").
  - `OWN_ADDR1/OWN_CITY/OWN_STATE/OWN_ZIPCD` — owner **mailing address**. For land investors doing direct mail, this is arguably the single most valuable field we don't ship.
  - `SALE_PRC2`, `SALE_YR2` — prior sale (trend signal).
  - `AV_NSD` (assessed), `TV_NSD` (taxable) — we currently alias `assessed_value` to just value (JV), which is *market* value, not assessed. Mislabels the number.
  - `S_LEGAL` — legal description (title/due-diligence aid).
  - `ACT_YR_BLT`, `TOT_LVG_AR` — sanity checks for "vacant" claims.
- **FEMA NFHL:** `EFF_DATE`/`DFIRM_ID` (how fresh the flood map is).
- **USFWS NWI wetlands:** polygon `ACRES` (how big the wetland is vs the lot).
- **USDA SDA soil:** `hydgrpdcd` (hydrologic group), flooding frequency class, water-table depth — all available in the same tabular service; far more actionable for septic than drainage class alone.
- **Geocodio:** `accuracy`, `accuracy_type`, per-candidate `address_components.state` (see §1).

## 3. BROKEN FLOWS (tested live)

1. **Batch data collapse under load** — road 1/10, power 0/10, waterway 0/10, elevation 6/10 (live 10-parcel run). Overpass gets ~60 concurrent requests from one 20-parcel chunk; it allows ~2. Everything 429s, `_safe()` swallows it, rows render with "Road data unavailable" and silent -5/-8 score penalties. **Scores are systematically wrong on batches.**
2. **Autocomplete is dead in production.** Frontend calls `/api/geocodio-suggest` (attached to `batchInput`, `modalBatchInput`, `singleParcelInput`). Live test: `?q=4420 ADAMO` → `{"suggestions":[]}` for every query. Root cause verified by querying the DOR layer directly: **any attribute `LIKE` on the statewide cadastral times out (>25s)** — even prefix matches; the endpoint's 5s client timeout can never be met. `/api/autocomplete` has the same flaw. The specced fix (statewide LIKE) will not work; needs a different source (see recommendations).
3. **Fuzzy geocodes score the wrong parcel.** Live: `1305 Grape Ave, Interlachen` → geocoded to **"Interlachen, FL 32148" (town centroid, place-level)** → returned PURSUE with soil/elevation for a random point in town. `117 Fig Ct, Poinciana` → **"201 Fig Ct, Kissimmee"** (different house number) scored as if it were the queried lot. `7150 NE 60th Ct, Williston` → street-center match without house number. No warning shown to the user in any of these cases.
4. **Elevation fallback endpoint is dead.** `collectors/elevation.py` falls back to `nationalmap.gov/epqs/pqs.php` — decommissioned years ago. Fallback can never succeed (part of why elevation was 6/10).
5. **Unauthenticated batch screening.** `/api/batch/stream` and `/api/batch` only enforce the weekly limit **if** an `Authorization` header is present. Anyone with curl can screen unlimited parcels on your Geocodio bill. `/api/batch/queue` has no auth at all.
6. **Flood collector retry can never fire.** `flood.py` retries after 1s with a 20s HTTP timeout, but `batch.py` wraps every collector in `wait_for(…, timeout=7s)`. Attempt 1 (slow) + sleep + attempt 2 can't fit; same mismatch for wetlands (12s), roads (20s), waterways (20s), powerlines (18s two sequential calls), parcel (15s). The outer 7s cap silently truncates collectors that were designed to be slower.
7. **Stale-cache field gaps:** results cached in Supabase (24h TTL, keyed by raw address string) are replayed verbatim — after any backend field upgrade, cached rows lack the new fields for a day, and "1305 grape ave…" vs "1305 Grape Ave …" are separate cache entries.
8. Auth flows (login/signup/reset/OAuth) could not be exercised headless without credentials — verified code paths only; needs a manual pass or test account (Stage 12).

## 4. CONCURRENCY BUGS

- `_API_SEM = Semaphore(120)` is global, not per-host. A 20-parcel chunk launches 12 collectors each = 240 tasks; 120 run at once, of which up to 60 hit **Overpass** (roads + waterways + powerlines share overpass-api.de, plus powerlines can issue a second call). Overpass free tier tolerates ~2 concurrent. Result: blanket 429s (proven live, §3.1).
- FL DOR gets 20 concurrent geometry queries per chunk plus FEMA 20, USGS 20, USDA 20 — most tolerate this, but there is no per-host budget anywhere.
- No retry on 429/timeout at the orchestration layer; `_safe()` returns the default (which the scorer then treats as a *data-driven* penalty, e.g. "Road data unavailable −5").
- Timeout inversion (§3.6): inner collector timeouts (12–20s) > outer `_API_TIMEOUT` (7s) > sensible per-host queue wait. Under per-host limits, queued waits will need the semaphore *outside* the timeout or road queries will starve.
- `_PARCEL_TIMEOUT = 15s` caps a whole parcel; with per-host queuing this must rise or the queue itself causes parcel timeouts.

## 5. GEOCODING BUGS

- `limit: 1`, first result taken, **no accuracy score check, no accuracy_type check, no state field check** (`geocodio.py`).
- FL check is a substring test on the formatted string (`", FL" in formatted`) in `batch.py` — it happens to catch "Devon" and the NC address today (both verified live → generic error), but:
  - a **low-accuracy FL match passes** (the three wrong-parcel cases in §3.3 all contain ", FL");
  - a wrong-state address yields the misleading message "Address not found in Florida — verify and retry" instead of naming the state it *did* match;
  - `geocode()` (single path) does no FL/accuracy checks at all — the check lives only in callers.
- No `status` contract (`ok/low_confidence/wrong_state/not_found`), no suggestions, so the frontend can only render one generic error.
- `geocode_batch()` maps results by Geocodio's echoed `query` string; safe today, but any Geocodio-side normalization of the query breaks address→result mapping silently (worth an index-based fallback since batch POST preserves order).

## 6. UI INCONSISTENCIES

- **Detail panel omits data we already ship.** `parcel_info` now contains `county, elevation_ft, flood_zone_subtype, base_flood_elevation_ft, wetland_type, habitat_species, contamination_sites, road_type/road_surface, waterway_type, evac_county` — the Parcel Details grid renders none of them (no County row, no Elevation, no Flood subtype, no Wetland type, no Habitat, no Road, no Contamination). Spec order from the redesign is also not followed (County/Elevation/Flood Subtype/Wetland/Habitat/Road missing entirely).
- **Owner truncation:** owners ending "&" render as-is (needs `OWN_NAME2` merge, else strip).
- **Acreage missing from detail panel** grid (it's in the table but not the panel details).
- Raw lowercase soil strings ("excessively drained") displayed without capitalization.
- `dpSignals` separator is a thin `|` (spec: middle dot ·); signal colors are inline `var(--red)` etc. — fine — but the map-modal sidebar uses raw hex (`#ef4444`, `#f59e0b`, `#22c55e`) instead of CSS vars.
- Duplicate deploy artifacts: root `index.html` and `frontend/index.html` are byte-identical and **both live** (Vercel serves root; Railway's `/` serves `frontend/`). Any edit must touch both or they drift. `frontend/parceliq_v5.html` (an old June build) is publicly reachable on the production domain.
- Legacy second results renderer (`buildExpandHtml` rcard/expand path) coexists with the current `res-trow` table — dead-ish code that still runs in some paths (processing-page error cards).
- Landing `<div id="page-landing">` scroll-reveal verified working; mobile 375px render OK in screenshot; hero, upload module, nav render correctly.
- `console.log` debug lines left in production autocomplete handler (lines ~7197, 7203).

## 7. WORDING ISSUES

- "Address not found in Florida — verify and retry" for wrong-state input (should name the state and say ParcelIQ covers FL only).
- "Geocoding failed — address not found in Florida" (batch) conflates *invalid address* with *non-FL address*.
- `getDORDescription` fallback prints "DOR code 12" — raw code leak for unknown/missing map entries; DOR UC map itself is complete (00–99) though some labels are jargon ("Acreage not zoned agric").
- Timeout flag "Timed out — retry this parcel" appears in `flags[]` — a system error presented as a parcel risk flag.
- `evac_risk` values ("very high") render lowercase mid-sentence in the panel ("Zone A · very high risk") — acceptable but inconsistent with Title Case elsewhere.
- Upgrade modal says "Upgrade to Pro for 2,000 parcels/week or Unlimited" while pricing page tiers are $29/$49/$79 — plan names/limits between modal, pricing page, and backend `WEEKLY_LIMIT=1000` need one source of truth.

## 8. DATA ACCURACY ISSUES

1. **Wrong-parcel scoring via fuzzy geocode** (§3.3) — the most dangerous inaccuracy: confidently-presented data for a different property.
2. **Point sampling vs parcel polygons:** every environmental check samples the geocode point (± a fixed buffer), not the parcel boundary. Wetlands uses ~50m/~110m boxes; **critical habitat uses a ±0.003° (~330m) envelope → an auto-KILL can fire from habitat 300m away on a neighbor's land.** EPA "contamination found" fires for sites up to 500m away. All auto-kill/flag language claims "on parcel."
3. **`assessed_value` is actually just value (JV)** — labeled "County tax estimate"; DOR's real assessed value is `AV_NSD` (not fetched). The "Tax exemption detected" flag (JV vs LND_VAL divergence >15%) is also questionable logic: on vacant land JV≈LND_VAL, but the comparison measures improvement share, not exemption.
4. **Data gaps scored as risks:** road unavailable −5, road unknown −8, no OSM powerline −5, county record missing −5. Under §4's outage these penalties applied to nearly every batch row — verdicts flip on infrastructure noise. Zone `NOT_MAPPED`/`ERROR` also silently treated as non-SFHA.
5. **auto-kill score display mismatch:** engine returns e.g. 40 after −60; frontend forces "0" for auto-kills; CSV export writes the raw score. Same parcel: three different numbers.
6. **SFHA inference:** when `SFHA_TF` is missing, zone A/AE/… is assumed SFHA — reasonable, but `sfha` also gates the AE auto-kill: `zone in SFHA_ZONES and sfha is True`; a FEMA row with `SFHA_TF='F'` but zone AE would *not* kill (data oddity but possible on LOMR-revised polygons).
7. **Evac zone `features[0]`** picked arbitrarily where polygons overlap; unknown zones map to risk "low".
8. **Cache staleness/keying** (§3.7) — 24h replay, per-formatting duplicates, cross-user shared (fine for public data, but "cached" rows still count against the *viewing* user's weekly usage in the frontend tally).
9. **Weekly usage has two different definitions:** backend `_weekly_usage()` counts `parcel_results` rows in a **rolling 7 days**; frontend counts `batches.total` since **Monday UTC**. Numbers on screen will disagree with the 429 enforcement.

## 9. MISSING FEATURES (for a product at this stage)

- **Working address autocomplete** (see §3.2 — needs a fast source; DOR LIKE cannot power it).
- Suggestions on failed/ambiguous geocodes ("did you mean…") — spec Stage 2/11.
- Owner mailing address + skip-trace-friendly CSV export (the fields investors mail to).
- Batch ETA on the processing screen; batch auto-naming from detected county.
- Dashboard rows: last-screened date + per-batch kill/pursue mini-summary.
- Navigation-away warning while a stream is in flight.
- Pricing page: manual-research vs ParcelIQ comparison.
- Backend API abuse protection (per-IP rate limit; require auth for batch endpoints) before any paid launch.
- Payment integration (pricing CTAs route to signup; no Stripe anywhere) — presumably known.
- Parcel comparison exists (`cmpModal`) — verify entry point visibility with 2–3 starred parcels (Stage 17.1 partially built).

## 10. QUICK WINS (<10 lines each)

1. Fix dead elevation fallback URL → `epqs.nationalmap.gov` retry or drop the fallback.
2. Fetch `OWN_NAME2` in `parcel_fl.py` and concatenate → kills the trailing-"&" bug properly.
3. Add County / Elevation / Road / Wetland type / Habitat / Flood subtype rows to the detail-panel grid (data already in `parcel_info`).
4. Name the actual state in wrong-state geocode errors.
5. Remove `console.log` from the autocomplete handler.
6. Remove/redirect `frontend/parceliq_v5.html` from production.
7. Capitalize soil drainage strings in the panel.
8. Move "Timed out — retry" out of `flags[]` into `error` only.
9. Backend `_weekly_usage`: switch rolling-7-days to Monday-UTC week start (matches frontend).
10. `require` auth on `/api/batch/queue` (one guard clause).

---

## Stage-plan reality check (what's already done vs the brief)

| Stage | Status found in repo/live |
|---|---|
| 1 Concurrency | **Not done** — global Sem(120), no per-host limits, no orchestration retry. Confirmed failing live. |
| 2 Geocoding accuracy | **Not done** — no accuracy/type/state checks in `geocodio.py`; crude `", FL"` guard only. |
| 3 Data completeness | **~90% done already** (county, BFE, subtype, evac_county_zone, EPA objects, sale-price heuristic, JS county fallback all present). Remaining: §2 missing DOR fields + frontend display (§6). |
| 4 Statewide suggest | **Not done, and specced approach is infeasible** — DOR LIKE times out (verified). Needs alternate source. |
| 5 Detail panel | **Mostly done** (dpSignals, dpWhy, translated flags, grid) — missing fields per §6, minor spec deviations. |
| 6 Filter panel | **Done** (FLAG_DEFS/FILTER_GROUPS match spec almost verbatim; counts from full results; filters persist across verdict tabs). |
| 7 Weekly reset / stop | **Frontend done** (getWeekStart, _rescreenAborted, usage refresh). **Backend still rolling-7-day** (§8.9). |
| 8 Row highlight | **Done** (`.row-active` CSS + toggle in openDetailPanel). |
| 9 Map modal | **Mostly done** (sidebar stats/signals, prev/next). Address duplication + "view full details" to verify visually. |
| 10 Autocomplete | **Wired but dead** (§3.2); also attached to paste textareas contrary to spec (newline guard mitigates). |
| 11 Error messaging | Partial — geocode-fail rows get inline fix-address UI in one renderer; no suggestion chips; generic messages. |
| 12 Auth | Not verifiable headless; code paths look coherent (async router guards, session restore). Needs manual/test-account pass. |
| 13 Settings | Page exists; **screening-rule toggles are frontend-only** (`RULES_DEF`/`_screeningRules` never sent to backend scorer) — non-functional UI per spec's own criterion. No analytics page (dashboard only). |
| 14 Landing | Renders correctly desktop+mobile; pricing $29/$49/$79 present; "API (Coming soon)" footer link is intentional. |
| 15 Consistency | Items catalogued in §6/§7. |
| 16 Validation script | **Already exists** (`backend/scripts/validate_addresses.py`) matching the spec, plus a results CSV from a prior run. |
| 17 Proactive | Compare modal exists; ETA, auto-naming, richer export, nav warning, dashboard summaries not found. |

**Priority order for the fix stages:** 1 (concurrency — scores are wrong today) → 2 (geocoding — wrong-parcel data) → 3 remainder (owner fields/labels) → 10/4 (autocomplete via a viable source) → 5/6/11 display gaps → 13 (remove or wire rule toggles) → the rest.
