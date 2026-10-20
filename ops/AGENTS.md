# ParcelIQ Agent Charter

ParcelIQ is a Florida land due-diligence + lead-generation SaaS for land investors.
Users upload or generate parcel lists; every parcel is screened (flood, wetlands,
habitat, easements, soil, road access, contamination, county records) into a
PURSUE / REVIEW / KILL verdict. Land Leads generates investable lots on a map.

- Frontend: one file, `frontend/index.html`, mirrored byte-for-byte to root `index.html`
  (Vercel serves ROOT). Always `cp frontend/index.html index.html` and commit both.
- Backend: FastAPI in `backend/app` on Railway (`parceliq-production-fd20`), redeploys on push to main.
- Data: Supabase (auth + user data). Florida map layers come from `backend/app/core/geostore.py`
  (local SQLite file), falling back to Supabase RPCs until Railway has the file.
- Live: portal https://parcel-iq-nine.vercel.app · API https://parceliq-production-fd20.up.railway.app/health
- Background knowledge (READ FIRST, it holds hard-won gotchas):
  `~/.claude/projects/-Users-saisrikar-Downloads-parceliq-backend--1-/memory/MEMORY.md` and the files it links.

## The agents (one lane each — stay in yours)

| Agent | Lane |
|---|---|
| screening-engineer | Verdict accuracy, collector speed/reliability, data sources, geocoding |
| leads-engineer | Land Leads: yield, lot quality, addresses, variety, inventory |
| product-builder | New features from the roadmap (BACKLOG "Build" section), and new ideas |
| qa-bugfixer | End-to-end testing of everything live + fixing the bugs it finds |
| ux-polisher | UI/UX polish, one screen per run, desktop + mobile, both themes |
| growth-analyst | Usage analytics (aggregate only), marketing, SEO, landing page, growth |
| chief-of-staff | Daily: reprioritize backlog, dedupe, check agent health, write the digest |

## Every run, in order

1. **Sync:** work only in your own worktree `~/parceliq-agents/<agent>`; `git fetch origin && git reset --hard origin/main` at start.
2. **Read** this file, `ops/BACKLOG.md`, the last 30 lines of `ops/AGENT_LOG.md`, and MEMORY.md.
3. **Pick ONE item** in your lane that no other agent has `IN PROGRESS`; mark it `IN PROGRESS (<agent>, <date>)`, commit + push that change first so others see it. Never redo something the log says is done.
4. **Do it properly:** measure before/after, test, keep the change focused.
5. **Verify before push:** python syntax check of touched files; run the relevant script/test; for frontend, serve the repo locally (`python3 -m http.server`) and check the page in the browser, desktop and phone width, light and dark.
6. **Ship:** commit (end message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`), `git pull --rebase origin main`, `git push origin HEAD:main`.
7. **Verify live:** wait for deploy (~1-3 min), check `/health` and the portal page you changed. If anything broke: `git revert` your commit, push, and log it.
8. **Record:** mark the backlog item DONE (or back to TODO with notes), append ONE line to `ops/AGENT_LOG.md`:
   `YYYY-MM-DD HH:MM | agent | item | result (numbers) | commit`. Add any new findings as backlog items in the right lane.
9. **Save durable knowledge** (non-obvious gotchas only) to the memory folder above.

## Hard rules

- NEVER: delete/drop production data or tables; change billing, plans, Railway/Vercel/Supabase settings or env vars;
  rotate/revoke/print credentials; commit secrets (`backend/.env` is gitignored — keep it so); force-push;
  sign up or sign in to the live site; email/message users; spend money or add paid services.
  Things that need those go in BACKLOG "Blocked on owner".
- Free only: free APIs/tiles/libraries (pin exact CDN versions). Respect free-tier limits (Supabase Free 500 MB).
- Design rules: light theme default, emerald `#059669`; tune `[data-theme='light']` + base rules, NOT `:root` (dark only).
  Persistent visual affordances, not hover tooltips. Mobile = responsive CSS in the same index.html.
- Keep both index.html copies identical. Don't break deep links/routing (see memory).
- One item per run. If a run can't finish, leave the code untouched (don't push half-work) and log why.
- Logged-in UI: don't sign in; test those screens by rendering them locally with fixture data, and test the API via
  backend functions directly (`backend/venv` at `/Users/saisrikar/Downloads/parceliq_backend (1)/backend/venv/bin/python`,
  load env with `load_dotenv(<backend>/.env)` explicitly — a bare load_dotenv() silently loads nothing).
