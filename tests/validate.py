"""
ParcelIQ validation suite — 8 varied Hillsborough County parcels.

Run with:
  cd tests && python validate.py

Requires server running on localhost:8000.
"""
import json
import sys
import time

import httpx

BASE = "http://localhost:8000"
TIMEOUT = 45.0

PARCELS = [
    {
        "name": "Angel Ln — vacant land (wetland)",
        "folio": "8.0000",
        "checks": ["address", "acreage", "flood_zone", "score_positive"],
    },
    {
        "name": "14201 Cyber Pl — commercial",
        "folio": "34907.0000",
        "checks": ["address", "flood_zone", "score_positive"],
    },
    {
        "name": "4420 Atwater Dr — residential",
        "folio": "153980.0000",
        "checks": ["address", "score_positive"],
    },
    {
        "name": "4802 W Longfellow Ave — Tampa bungalow",
        "folio": None,
        "address_query": "4802 W Longfellow Ave",
        "checks": ["score_positive"],
    },
    {
        "name": "3315 W Sligh Ave — commercial lot",
        "folio": None,
        "address_query": "3315 W Sligh Ave",
        "checks": ["score_positive"],
    },
    {
        "name": "Suggest endpoint — partial '4420'",
        "suggest_query": "4420",
        "min_results": 3,
    },
    {
        "name": "Suggest endpoint — partial 'Bayshore'",
        "suggest_query": "Bayshore",
        "min_results": 2,
    },
    {
        "name": "Suggest endpoint — partial '100 N Ashley'",
        "suggest_query": "100 N Ashley",
        "min_results": 1,
    },
]

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"


def _resolve_folio(address_query: str, client: httpx.Client) -> str | None:
    resp = client.get(f"{BASE}/api/search", params={"q": address_query}, timeout=TIMEOUT)
    if resp.status_code != 200:
        return None
    candidates = resp.json().get("candidates", [])
    return candidates[0]["folio"] if candidates else None


def check_report(folio: str, checks: list[str], client: httpx.Client) -> tuple[bool, str]:
    try:
        resp = client.get(f"{BASE}/api/report/{folio}", timeout=TIMEOUT)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        data = resp.json()
    except Exception as exc:
        return False, str(exc)

    pr = data.get("property_report", {})
    bi = data.get("buyer_insights", {})
    prop = pr.get("property", {})
    buildability = bi.get("buildability", {})
    score = buildability.get("score")
    label = buildability.get("label", "")

    errors = []

    if "address" in checks:
        if not prop.get("address"):
            errors.append("address is null")

    if "acreage" in checks:
        acres = prop.get("acreage_gross")
        if acres is None:
            errors.append("acreage_gross is null")
        elif acres <= 0:
            errors.append(f"acreage_gross <= 0: {acres}")

    if "flood_zone" in checks:
        flood = (pr.get("environmental") or {}).get("flood") or {}
        if not flood.get("fld_zone"):
            errors.append("fld_zone missing (check parcel has geometry)")

    if "score_positive" in checks:
        if score is None:
            errors.append("buildability score missing")
        elif score < 0 or score > 100:
            errors.append(f"score out of range: {score}")
        if not label:
            errors.append("buildability label missing")
        findings = bi.get("top_findings", [])
        if not isinstance(findings, list):
            errors.append("top_findings not a list")

    if errors:
        return False, "; ".join(errors)

    summary = f"score={score} ({label}), {len(bi.get('top_findings', []))} findings"
    return True, summary


def check_suggest(q: str, min_results: int, client: httpx.Client) -> tuple[bool, str]:
    try:
        resp = client.get(f"{BASE}/api/suggest", params={"q": q}, timeout=15.0)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        data = resp.json()
    except Exception as exc:
        return False, str(exc)

    results = data.get("results", [])
    if len(results) < min_results:
        return False, f"got {len(results)} results, expected >= {min_results}"

    for r in results:
        if not r.get("full_address"):
            return False, "result missing full_address"

    return True, f"{len(results)} results returned"


def main():
    results_table = []

    with httpx.Client(timeout=TIMEOUT) as client:
        # Health check
        try:
            h = client.get(f"{BASE}/health", timeout=5)
            if h.status_code != 200:
                print(f"\n  Server not responding at {BASE}\n")
                sys.exit(1)
        except Exception as exc:
            print(f"\n  Cannot connect to {BASE}: {exc}\n")
            sys.exit(1)

        print(f"\nParcelIQ Validation — {BASE}\n{'─' * 70}")

        for parcel in PARCELS:
            name = parcel["name"]

            # Suggest-only test
            if "suggest_query" in parcel:
                ok, detail = check_suggest(parcel["suggest_query"], parcel["min_results"], client)
                status = PASS if ok else FAIL
                print(f"  {status}  {name}")
                print(f"        {detail}")
                results_table.append(ok)
                continue

            # Resolve folio
            folio = parcel.get("folio")
            if not folio and parcel.get("address_query"):
                folio = _resolve_folio(parcel["address_query"], client)
                if not folio:
                    print(f"  {SKIP}  {name}")
                    print(f"        Could not resolve folio from address_query")
                    results_table.append(None)
                    continue

            if not folio:
                print(f"  {SKIP}  {name}")
                results_table.append(None)
                continue

            ok, detail = check_report(folio, parcel.get("checks", []), client)
            status = PASS if ok else FAIL
            print(f"  {status}  {name} [{folio}]")
            print(f"        {detail}")
            results_table.append(ok)

    print(f"\n{'─' * 70}")
    passed  = sum(1 for r in results_table if r is True)
    failed  = sum(1 for r in results_table if r is False)
    skipped = sum(1 for r in results_table if r is None)
    total   = len(results_table)
    print(f"  Results: {passed}/{total - skipped} passed, {failed} failed, {skipped} skipped\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
