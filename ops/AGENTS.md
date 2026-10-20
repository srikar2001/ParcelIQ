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
- Coordination: `agents/board` branch holds ops/BACKLOG.md, ops/AGENT_LOG.md, ops/READY.md (branches awaiting owner merge).
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

**Agents never push to `main`.** `main` deploys straight to the live site; the owner reviews and merges.
Code goes on a branch + pull request. Coordination files live on the shared branch `agents/board`.

1. **Sync:** work only in your own worktree `~/parceliq-agents/<agent>`; `git fetch origin` at start.
2. **Read the board:** `git show origin/agents/board:ops/BACKLOG.md`, `git show origin/agents/board:ops/AGENT_LOG.md | tail -30`,
   `git show origin/agents/board:ops/READY.md`, this charter (`origin/main:ops/AGENTS.md`), and MEMORY.md.
3. **Your open work first:** if you have 3+ unmerged branches listed in READY.md, don't start new work — rebase them
   on `origin/main`, fix conflicts/review issues, re-test, and stop.
4. **Claim ONE item** in your lane that isn't `IN PROGRESS`: on the board branch
   (`git checkout -B board origin/agents/board`), mark it `IN PROGRESS (<agent>, <date>)`, commit, `git push origin board:agents/board`
   (on rejection: `git pull --rebase origin agents/board` and push again). Never redo something the log/READY says is done.
5. **Build on a branch:** `git checkout -B agent/<agent>/<short-slug> origin/main`. Measure before/after, test, keep it focused.
6. **Verify locally:** python syntax check of touched files; run the relevant script/test; for frontend, serve the repo
   locally (`python3 -m http.server`) and check in the browser at desktop and phone width, light and dark. Vercel also builds
   a preview for every pushed branch — check it if it appears.
7. **Ship as a PR:** commit (end message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`), push the branch
   (`git push -u origin agent/<agent>/<slug>`). If `gh auth status` succeeds, `gh pr create --base main` with a body:
   what/why, before→after numbers, how it was tested, screenshots paths, risk. Otherwise record the compare link
   `https://github.com/srikar2001/ParcelIQ/compare/main...agent/<agent>/<slug>?expand=1`.
8. **Record on the board:** set the item to `IN REVIEW (<branch>)`, add a line to `ops/READY.md`
   (`- [ ] <branch> — <one-line summary> — <PR or compare link> — risk: low/med/high`), append ONE line to
   `ops/AGENT_LOG.md` (`YYYY-MM-DD HH:MM | agent | item | result (numbers) | branch`), add new findings as backlog items. Push the board.
9. **After the owner merges** (branch is in `origin/main`): the next run of that agent checks the live site, marks the item DONE,
   ticks it in READY.md. If live is broken by it, open a revert PR immediately and flag it at the top of READY.md.
10. **Save durable knowledge** (non-obvious gotchas only) to the memory folder above.

## Hard rules

- NEVER: delete/drop production data or tables; change billing, plans, Railway/Vercel/Supabase settings or env vars;
  rotate/revoke/print credentials; commit secrets (`backend/.env` is gitignored — keep it so); force-push;
  sign up or sign in to the live site; email/message users; spend money or add paid services.
  Things that need those go in BACKLOG "Blocked on owner".
- Free only: free APIs/tiles/libraries (pin exact CDN versions). Respect free-tier limits (Supabase Free 500 MB).
- Design rules: light theme default, emerald `#059669`; tune `[data-theme='light']` + base rules, NOT `:root` (dark only).
  Persistent visual affordances, not hover tooltips. Mobile = responsive CSS in the same index.html.
- Keep both index.html copies identical. Don't break deep links/routing (see memory).
- One item per run. Never push to main, never merge your own PR, never force-push. If a run can't finish, push nothing half-done; log why.
- Logged-in UI: don't sign in; test those screens by rendering them locally with fixture data, and test the API via
  backend functions directly (`backend/venv` at `/Users/saisrikar/Downloads/parceliq_backend (1)/backend/venv/bin/python`,
  load env with `load_dotenv(<backend>/.env)` explicitly — a bare load_dotenv() silently loads nothing).
