"""
Full end-to-end test suite for ParcelIQ.
Run: python tests/test_e2e.py
Requires server running on localhost:8000.
"""
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'backend', '.env'))

BASE = "http://localhost:8000"
TIMEOUT = 45.0
DEMO_FOLIO = "8.0000"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

results = []

def record(name, ok, note=""):
    if ok is None:
        status = SKIP
    elif ok:
        status = PASS
    else:
        status = FAIL
    results.append((name, ok, note))
    print(f"  {status}  {name}")
    if note:
        print(f"        {note}")


def run():
    print(f"\nParcelIQ — Full End-to-End Test")
    print(f"Server: {BASE} | Demo folio: {DEMO_FOLIO}")
    print("─" * 65)

    client = httpx.Client(timeout=TIMEOUT)

    # Health check
    try:
        r = client.get(f"{BASE}/health")
        assert r.status_code == 200
        print(f"  {PASS}  Server reachable\n")
    except Exception as e:
        print(f"  {FAIL}  Server not reachable: {e}")
        sys.exit(1)

    # ── TEST 1: Suggest/Search
    try:
        r = client.get(f"{BASE}/api/suggest", params={"q": "4420"})
        d = r.json()
        results_list = d.get("results", [])
        ok = len(results_list) >= 3 and all(r.get("full_address") for r in results_list)
        record("TEST 1 — Suggest/Search",
               ok,
               f"{len(results_list)} results | first: {results_list[0]['full_address'] if results_list else 'none'}")
    except Exception as e:
        record("TEST 1 — Suggest/Search", False, str(e))

    # ── TEST 2: Full report for demo property
    try:
        t0 = time.time()
        r = client.get(f"{BASE}/api/report/{DEMO_FOLIO}")
        elapsed = time.time() - t0
        cache_status = r.headers.get("X-Cache", "unknown")
        d = r.json()

        pr = d.get("property_report", {})
        bi = d.get("buyer_insights", {})
        prop = pr.get("property", {})
        env = pr.get("environmental", {})
        buildability = bi.get("buildability", {})
        sources = pr.get("source_evidence", [])

        addr_ok      = "angel" in (prop.get("address") or "").lower() or "19931" in (prop.get("address") or "")
        acreage_ok   = abs((prop.get("acreage_gross") or 0) - 4.71) < 0.1
        flood_ok     = (env.get("flood") or {}).get("fld_zone") == "X"
        wetland_ok   = (env.get("wetlands") or {}).get("found") is True
        score_ok     = 0 < (buildability.get("score") or 0) <= 100
        sources_ok   = len(sources) >= 8

        ok = all([addr_ok, acreage_ok, flood_ok, wetland_ok, score_ok, sources_ok])
        record("TEST 2 — Full Report (8.0000)",
               ok,
               (f"addr={prop.get('address')} | ac={prop.get('acreage_gross')} | "
                f"flood={env.get('flood', {}).get('fld_zone')} | "
                f"wetland={env.get('wetlands', {}).get('found')} | "
                f"score={buildability.get('score')} ({buildability.get('label')}) | "
                f"sources={len(sources)} | cache={cache_status} | {elapsed:.1f}s"))
    except Exception as e:
        record("TEST 2 — Full Report (8.0000)", False, str(e))
        bi = {}
        buildability = {}

    # ── TEST 3: Different parcel — confirm different data
    try:
        r_sug = client.get(f"{BASE}/api/suggest", params={"q": "4802 W Longfellow"})
        sug = r_sug.json().get("results", [])
        if not sug:
            record("TEST 3 — Different Parcel", None, "No suggest results for 4802 W Longfellow")
        else:
            other_folio = sug[0].get("folio")
            r2 = client.get(f"{BASE}/api/report/{other_folio}")
            d2 = r2.json()
            pr2 = d2.get("property_report", {})
            prop2 = pr2.get("property", {})
            # Must be a different address from demo
            different = (prop2.get("folio") or "") != DEMO_FOLIO
            record("TEST 3 — Different Parcel",
                   different,
                   f"folio={prop2.get('folio')} | addr={prop2.get('address')} | ac={prop2.get('acreage_gross')}")
    except Exception as e:
        record("TEST 3 — Different Parcel", False, str(e))

    # ── TEST 4: Cache (second call should be faster)
    try:
        t1 = time.time()
        r_first = client.get(f"{BASE}/api/report/{DEMO_FOLIO}")
        t1_elapsed = time.time() - t1
        cache1 = r_first.headers.get("X-Cache", "?")

        t2 = time.time()
        r_second = client.get(f"{BASE}/api/report/{DEMO_FOLIO}")
        t2_elapsed = time.time() - t2
        cache2 = r_second.headers.get("X-Cache", "?")

        ok = cache2 == "HIT" or t2_elapsed < t1_elapsed * 0.5
        record("TEST 4 — Caching",
               ok,
               f"1st={t1_elapsed:.2f}s ({cache1}) | 2nd={t2_elapsed:.2f}s ({cache2})")
    except Exception as e:
        record("TEST 4 — Caching", False, str(e))

    # ── TEST 5: AI Narrative
    google_key = os.environ.get("GOOGLE_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    has_ai = (google_key and google_key != "paste_your_NEW_gemini_key_here") or bool(anthropic_key)

    if not has_ai:
        record("TEST 5 — AI Narrative", None, "SKIP — no GOOGLE_API_KEY or ANTHROPIC_API_KEY in .env")
    else:
        try:
            r = client.get(f"{BASE}/api/narrative/{DEMO_FOLIO}", timeout=60.0)
            if r.status_code == 503:
                record("TEST 5 — AI Narrative", None, "SKIP — AI key not configured")
            elif r.status_code != 200:
                record("TEST 5 — AI Narrative", False, f"HTTP {r.status_code}: {r.text[:80]}")
            else:
                d = r.json()
                narrative = d.get("narrative", "")
                ok = len(narrative) > 100
                record("TEST 5 — AI Narrative",
                       ok,
                       f"cached={d.get('cached')} | {len(narrative)} chars | first 100: {narrative[:100]}…")
        except Exception as e:
            record("TEST 5 — AI Narrative", False, str(e))

    # ── TEST 6: Supabase Storage
    try:
        from supabase import create_client
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        if not url or not key:
            record("TEST 6 — Supabase Storage", None, "SKIP — no SUPABASE_URL/KEY")
        else:
            sb = create_client(url, key)
            searches = sb.table("searches").select("id,folio").eq("folio", DEMO_FOLIO).execute()
            reports_row = sb.table("reports").select("folio,buildability_score,updated_at").eq("folio", DEMO_FOLIO).execute()
            searches_count = len(searches.data)
            report_cached = len(reports_row.data) > 0
            ok = searches_count > 0 and report_cached
            note = f"searches rows={searches_count} | report cached={report_cached}"
            if report_cached:
                note += f" | score={reports_row.data[0].get('buildability_score')}"
            record("TEST 6 — Supabase Storage", ok, note)
    except Exception as e:
        record("TEST 6 — Supabase Storage", False, str(e)[:80])

    # ── Print final table
    print("\n" + "─" * 65)
    print(f"{'TEST':<30} {'STATUS':<8} NOTE")
    print("─" * 65)
    for name, ok, note in results:
        if ok is None:
            status = SKIP
        elif ok:
            status = PASS
        else:
            status = FAIL
        short_note = (note[:35] + "…") if len(note) > 35 else note
        print(f"  {name:<30} {status}   {short_note}")

    passed  = sum(1 for _, ok, _ in results if ok is True)
    failed  = sum(1 for _, ok, _ in results if ok is False)
    skipped = sum(1 for _, ok, _ in results if ok is None)
    print("─" * 65)
    print(f"\n  Final: {passed} passed, {failed} failed, {skipped} skipped\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    run()
