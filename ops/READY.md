# Ready for owner review

Branches agents finished, newest last. Owner merges the PR (or opens it from the compare link). A broken-after-merge revert gets flagged at the TOP.

- [x] MERGED 2026-10-20 (4c2513c) agent/product-builder/3d-land-view — B1: lazy MapLibre 3D terrain+satellite toggle on Deal Review + Land Leads maps (parcel outline draped, tilt/rotate, mobile) — https://github.com/srikar2001/ParcelIQ/compare/main...agent/product-builder/3d-land-view?expand=1 — risk: low (off by default, frontend only) — qa 2026-10-20: index parity ok, merges clean on main, maplibre-gl@4.7.1 CDN 200
- [x] MERGED 2026-10-20 (4c2513c) agent/qa-bugfixer/golden-set — failed flood/wetland/habitat checks no longer read as clean (+5 'No wetlands' on error removed, -5 per gap) + golden-set runner & offline test — https://github.com/srikar2001/ParcelIQ/compare/main...agent/qa-bugfixer/golden-set?expand=1 — risk: low (only changes scores when a kill-check fails)
- [x] MERGED 2026-10-20 (4c2513c) agent/qa-bugfixer/golden-baseline — Q4: tests/golden.json baseline (42 pts, real local geostore: 24 PURSUE/18 KILL, re-run 0 drift); stacked on golden-set, merge that first — https://github.com/srikar2001/ParcelIQ/compare/main...agent/qa-bugfixer/golden-baseline?expand=1 — risk: low (test data only)
