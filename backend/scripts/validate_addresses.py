# validate_addresses.py — Independent ParcelIQ output validator
#
# Usage:
#   python backend/scripts/validate_addresses.py <input_addresses.csv> [piq_export.csv]
#
# <input_addresses.csv>  — CSV with at least one column named "address" (one address per row).
#                          Addresses containing commas MUST be quoted in the CSV, e.g.:
#                            address
#                            "877 Barton Blvd, Rockledge, FL 32955"
#                          Google Sheets / Excel exports do this automatically.
#                          You can also generate a valid CSV with:
#                            python3 -c "import csv,sys; w=csv.writer(sys.stdout); \
#                              w.writerow(['address']); \
#                              [w.writerow([a]) for a in open('addrs.txt').read().splitlines()]"
# [piq_export.csv]       — Optional: ParcelIQ export CSV (columns: Address, Verdict, Score,
#                          Auto Kill, Kill Reason, Flags, Positives, Acreage, Owner,
#                          Last Sale, Land Use) to diff against independent results
#
# Output: backend/scripts/validation_results.csv
#
# Note on parcel data: the Census Bureau geocoder places points at street centerline
# interpolation positions, which may land just outside a parcel polygon. Owner/acreage/sale
# columns may be empty even for valid addresses — this is expected and highlights one real
# difference between the Census and Geocodio coordinate sources.
#
# This script intentionally does NOT import anything from backend/app — it re-implements
# its own queries so bugs in the app's own field parsing or coordinate transforms show up
# as mismatches rather than being silently confirmed.
# Geocoder: US Census Bureau (free, no API key).

import csv
import sys
import time
from pathlib import Path

try:
    import requests as _requests
except ImportError:
    print("ERROR: 'requests' is not installed. Run: pip install requests")
    sys.exit(1)

OUTPUT_CSV = Path(__file__).parent / "validation_results.csv"

_SESSION = _requests.Session()
_SESSION.headers.update({"User-Agent": "ParcelIQ-Validator/1.0"})

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, params: dict, timeout: int = 10) -> dict:
    try:
        r = _SESSION.get(url, params=params, timeout=timeout)
        return r.json()
    except Exception as exc:
        return {"_error": str(exc)}


def _arcgis_point(url: str, lat: float, lng: float, out_fields: str, timeout: int = 10) -> dict:
    params = {
        "geometry":     f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR":         "4326",
        "spatialRel":   "esriSpatialRelIntersects",
        "outFields":    out_fields,
        "returnGeometry": "false",
        "f":            "json",
        "where":        "1=1",
    }
    return _get(url, params, timeout=timeout)


# ---------------------------------------------------------------------------
# Step 1 — Census Bureau geocoder
# ---------------------------------------------------------------------------

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

def geocode_census(address: str) -> tuple[float | None, float | None]:
    data = _get(CENSUS_URL, {
        "address":   address,
        "benchmark": "Public_AR_Current",
        "format":    "json",
    }, timeout=15)
    try:
        match = data["result"]["addressMatches"][0]
        coords = match["coordinates"]
        return float(coords["y"]), float(coords["x"])  # lat, lng
    except (KeyError, IndexError, TypeError, ValueError):
        return None, None


# ---------------------------------------------------------------------------
# Step 2 — Independent source queries
# ---------------------------------------------------------------------------

FEMA_URL      = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
WETLANDS_URL  = "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/USA_Wetlands/FeatureServer/0/query"
HABITAT_URL   = "https://services.arcgis.com/QVENGdaPbd4LUkLV/arcgis/rest/services/USFWS_Critical_Habitat/FeatureServer/0/query"
EPA_URL       = "https://echodata.epa.gov/echo/rest/services/ECHO/Superfund_Sites_FRS/MapServer/0/query"
ELEVATION_URL = "https://epqs.nationalmap.gov/v1/json"
PARCEL_URL    = "https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/Florida_Statewide_Cadastral/FeatureServer/0/query"
EVAC_URL      = "https://services1.arcgis.com/CY1LXxl9zlJeBuRZ/ArcGIS/rest/services/2024_Evacuation_Zones/FeatureServer/0/query"


def query_flood(lat: float, lng: float) -> str:
    data = _arcgis_point(FEMA_URL, lat, lng, "FLD_ZONE,SFHA_TF")
    feats = data.get("features", [])
    if not feats:
        return "X"
    attrs = feats[0].get("attributes", {})
    zone = attrs.get("FLD_ZONE") or "X"
    sfha = attrs.get("SFHA_TF")
    return f"{zone}" + (f" (SFHA:{sfha})" if sfha else "")


def query_wetland(lat: float, lng: float) -> str:
    data = _arcgis_point(WETLANDS_URL, lat, lng, "WETLAND_TYPE")
    feats = data.get("features", [])
    if not feats:
        return "none"
    return feats[0].get("attributes", {}).get("WETLAND_TYPE") or "none"


def query_habitat(lat: float, lng: float) -> str:
    data = _arcgis_point(HABITAT_URL, lat, lng, "comname")
    feats = data.get("features", [])
    if not feats:
        return "none"
    names = list({f.get("attributes", {}).get("comname") or "" for f in feats if f.get("attributes", {}).get("comname")})
    return "; ".join(names[:3]) if names else "none"


def query_epa(lat: float, lng: float) -> str:
    # EPA ECHO uses a distance-based buffer (1-mile radius), not a spatial intersect
    params = {
        "geometry":     f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR":         "4326",
        "distance":     "1",
        "units":        "esriSRUnit_StatuteMile",
        "spatialRel":   "esriSpatialRelIntersects",
        "outFields":    "FAC_NAME",
        "returnGeometry": "false",
        "f":            "json",
    }
    data = _get(EPA_URL, params)
    feats = data.get("features", [])
    if not feats:
        return "none"
    names = [f.get("attributes", {}).get("FAC_NAME") or "" for f in feats[:3]]
    return "; ".join(n for n in names if n) or "none"


def query_elevation(lat: float, lng: float) -> str:
    data = _get(ELEVATION_URL, {"x": lng, "y": lat, "units": "Feet"})
    try:
        val = data.get("value") or data.get("feet")
        if val is None:
            val = data["USGS_Elevation_Point_Query_Service"]["Elevation_Query"]["Elevation"]
        return str(round(float(val), 1))
    except Exception:
        return "unknown"


def query_parcel(lat: float, lng: float) -> dict:
    data = _arcgis_point(PARCEL_URL, lat, lng, "OWN_NAME,DOR_UC,LND_SQFOOT,SALE_PRC1,SALE_YR1")
    feats = data.get("features", [])
    if not feats:
        return {}
    attrs = feats[0].get("attributes", {})
    sqft = attrs.get("LND_SQFOOT")
    acreage = round(sqft / 43560, 2) if sqft else None
    return {
        "owner":     attrs.get("OWN_NAME") or "",
        "acreage":   str(acreage) if acreage is not None else "",
        "last_sale": str(attrs.get("SALE_PRC1") or ""),
        "sale_year": str(attrs.get("SALE_YR1") or ""),
    }


def query_evac(lat: float, lng: float) -> str:
    data = _arcgis_point(EVAC_URL, lat, lng, "EZone")
    feats = data.get("features", [])
    if not feats:
        return "none"
    return feats[0].get("attributes", {}).get("EZone") or "none"


# ---------------------------------------------------------------------------
# Step 3 — Load optional ParcelIQ export CSV
# ---------------------------------------------------------------------------

def load_piq_export(path: str) -> dict[str, dict]:
    """Return {address_lower: {flood_zone, wetland, owner, acreage, last_sale, evac_zone}}"""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            addr_key = (row.get("Address") or row.get("address") or "").strip().lower()
            if not addr_key:
                continue
            # Parse flags/positives for flood and wetland signals
            flags = (row.get("Flags") or "").lower()
            positives = (row.get("Positives") or "").lower()
            flood = "AE" if "flood" in flags and "sfha" in flags else \
                    "AE" if "100-year" in flags else \
                    "X" if "minimal flood" in positives else "unknown"
            wetland = "yes" if "wetland" in flags else "none"
            out[addr_key] = {
                "flood_zone": flood,
                "wetland":    wetland,
                "owner":      (row.get("Owner") or "").strip(),
                "acreage":    (row.get("Acreage") or "").strip(),
                "last_sale":  (row.get("Last Sale") or "").strip(),
                "evac_zone":  "",  # not in standard export
            }
    return out


# ---------------------------------------------------------------------------
# Step 4 — Diff logic
# ---------------------------------------------------------------------------

def _norm(v: str) -> str:
    return str(v).strip().upper()


def build_match_summary(row: dict, piq: dict) -> str:
    mismatches = []

    def check(field_label: str, ind_val: str, piq_val: str):
        if piq_val and _norm(ind_val) != _norm(piq_val):
            mismatches.append(f"{field_label} MISMATCH: independent={ind_val} piq={piq_val}")

    check("flood_zone", row["independent_flood_zone"],  piq.get("flood_zone", ""))
    check("wetland",    row["independent_wetland"],     piq.get("wetland", ""))
    check("owner",      row["independent_owner"],       piq.get("owner", ""))
    check("acreage",    row["independent_acreage"],     piq.get("acreage", ""))
    check("last_sale",  row["independent_last_sale"],   piq.get("last_sale", ""))
    return "; ".join(mismatches) if mismatches else "OK"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python backend/scripts/validate_addresses.py <input_addresses.csv> [piq_export.csv]")
        sys.exit(1)

    input_path = sys.argv[1]
    piq_path   = sys.argv[2] if len(sys.argv) >= 3 else None

    # Load addresses
    addresses = []
    with open(input_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            addr = row.get("address") or row.get("Address") or ""
            if addr.strip():
                addresses.append(addr.strip())

    if not addresses:
        print("No addresses found in input CSV. Column must be named 'address'.")
        sys.exit(1)

    # Load ParcelIQ export if provided
    piq_data: dict[str, dict] = {}
    if piq_path:
        piq_data = load_piq_export(piq_path)
        print(f"Loaded {len(piq_data)} rows from ParcelIQ export: {piq_path}")

    has_piq = bool(piq_data)

    base_fields = [
        "address", "census_lat", "census_lng",
        "independent_flood_zone", "independent_wetland", "independent_habitat",
        "independent_epa_site", "independent_elevation_ft",
        "independent_owner", "independent_acreage", "independent_last_sale",
        "independent_evac_zone",
    ]
    piq_fields = [
        "piq_flood_zone", "piq_wetland", "piq_owner",
        "piq_acreage", "piq_last_sale", "piq_evac_zone",
        "match_summary",
    ] if has_piq else []
    fieldnames = base_fields + piq_fields

    results = []
    total = len(addresses)
    print(f"\nValidating {total} address(es)...\n")

    # Track hosts already hit in this loop iteration to insert delays between different hosts
    for i, address in enumerate(addresses, 1):
        print(f"[{i}/{total}] {address}")

        # --- Census geocode ---
        lat, lng = geocode_census(address)
        time.sleep(0.5)

        if lat is None:
            print(f"  ✗ Census geocode failed — skipping source queries")
            row = {f: "" for f in fieldnames}
            row["address"] = address
            row["census_lat"] = "geocode_failed"
            row["census_lng"] = "geocode_failed"
            if has_piq:
                row["match_summary"] = "geocode_failed"
            results.append(row)
            continue

        print(f"  ✓ Census: {lat:.5f}, {lng:.5f}")

        # --- Source queries (sequential, 1s between different hosts) ---
        flood = query_flood(lat, lng);      time.sleep(1)
        wetland = query_wetland(lat, lng);  time.sleep(1)
        habitat = query_habitat(lat, lng);  time.sleep(1)
        epa = query_epa(lat, lng);          time.sleep(1)
        elev = query_elevation(lat, lng);   time.sleep(1)
        parcel = query_parcel(lat, lng);    time.sleep(1)
        evac = query_evac(lat, lng);        time.sleep(0.5)

        row = {
            "address":                  address,
            "census_lat":               round(lat, 6),
            "census_lng":               round(lng, 6),
            "independent_flood_zone":   flood,
            "independent_wetland":      wetland,
            "independent_habitat":      habitat,
            "independent_epa_site":     epa,
            "independent_elevation_ft": elev,
            "independent_owner":        parcel.get("owner", ""),
            "independent_acreage":      parcel.get("acreage", ""),
            "independent_last_sale":    parcel.get("last_sale", ""),
            "independent_evac_zone":    evac,
        }

        if has_piq:
            piq_row = piq_data.get(address.lower(), {})
            row["piq_flood_zone"] = piq_row.get("flood_zone", "")
            row["piq_wetland"]    = piq_row.get("wetland", "")
            row["piq_owner"]      = piq_row.get("owner", "")
            row["piq_acreage"]    = piq_row.get("acreage", "")
            row["piq_last_sale"]  = piq_row.get("last_sale", "")
            row["piq_evac_zone"]  = piq_row.get("evac_zone", "")
            row["match_summary"]  = build_match_summary(row, piq_row)

        results.append(row)

        # Brief pause between addresses to be polite to all APIs
        if i < total:
            time.sleep(1)

    # --- Write output CSV ---
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nResults written to: {OUTPUT_CSV}")

    # --- Plain-language summary ---
    geocode_failed = [r for r in results if r.get("census_lat") == "geocode_failed"]
    checked        = [r for r in results if r.get("census_lat") != "geocode_failed"]

    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Total addresses:    {total}")
    print(f"Geocode failures:   {len(geocode_failed)}")
    print(f"Successfully checked: {len(checked)}")

    if has_piq:
        ok        = [r for r in checked if r.get("match_summary") == "OK"]
        mismatched = [r for r in checked if r.get("match_summary") not in ("OK", "", "geocode_failed")]
        print(f"Fully matched:      {len(ok)}")
        print(f"Had mismatches:     {len(mismatched)}")
        if mismatched:
            print("\nMismatched addresses:")
            for r in mismatched:
                print(f"  • {r['address']}")
                for detail in r["match_summary"].split("; "):
                    print(f"      {detail}")
    else:
        print("\n(No ParcelIQ export provided — diff comparison skipped)")
        print("Re-run with a second argument to compare against ParcelIQ output.")

    if geocode_failed:
        print("\nCensus geocode failures (manually verify these addresses):")
        for r in geocode_failed:
            print(f"  • {r['address']}")

    print("=" * 60)


if __name__ == "__main__":
    main()
