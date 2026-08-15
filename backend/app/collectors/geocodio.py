import os
import re

import httpx

# Street-type suffixes, directionals, unit words and state tokens that are NOT,
# on their own, an actual street/place NAME. Used only to spot junk like
# "232 street" (a number + a generic word, no real name) so we don't pay the
# geocoder for it.
_ADDR_GENERIC = {
    "st", "street", "ave", "avenue", "av", "rd", "road", "blvd", "boulevard",
    "ln", "lane", "dr", "drive", "ct", "court", "pl", "place", "ter", "terrace",
    "way", "cir", "circle", "hwy", "highway", "pkwy", "parkway", "trl", "trail",
    "loop", "run", "path", "pass", "row", "pike", "plaza", "sq", "square",
    "aly", "alley", "byp", "bypass", "xing", "crossing", "cres", "crescent",
    "n", "s", "e", "w", "ne", "nw", "se", "sw", "north", "south", "east", "west",
    "fl", "florida", "us", "usa", "apt", "unit", "ste", "suite", "lot", "no",
    "county", "co", "cr", "sr",
}


def looks_like_address(address: str) -> bool:
    """Cheap, LENIENT local sanity check run before we spend a paid geocoder
    lookup. It only rejects the clearly-unusable (empty, too short, pure
    numbers/symbols, or a number + a generic word with no real name like
    "232 street") — anything that could plausibly be a real address passes, so
    a legitimate parcel is never dropped. Junk rows still get an ERROR result;
    they just don't cost an API credit."""
    s = (address or "").strip()
    if len(s) < 6:
        return False
    letters = re.findall(r"[a-zA-Z]+", s)
    if not letters:
        return False  # pure numbers / symbols, e.g. "12345" or "###"
    # A real name = a word that isn't just a street-type/directional/state token.
    name_words = [w for w in letters if w.lower() not in _ADDR_GENERIC]
    has_zip = re.search(r"\b\d{5}\b", s) is not None
    # No actual name AND no ZIP -> too vague to be a real parcel address.
    return bool(name_words) or has_zip

# Accuracy types that pin a coordinate to (or adjacent to) the actual lot.
# Notably excluded: "place" (town centroid), "state", "county", "zip" — these
# used to slip through and score a parcel at the middle of a town.
ACCEPTED_ACCURACY_TYPES = {
    "rooftop", "range_interpolation", "point",
    "nearest_rooftop_match", "street_center",
}
MIN_ACCURACY = 0.85
SUGGEST_MIN_ACCURACY = 0.70

_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "Washington DC", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}


# ── Address normalization ────────────────────────────────────────────────────
# ParcelIQ is Florida-only, so any address that doesn't already name a state can
# be biased to FL. This turns the sloppy addresses clients actually send —
# "1432 coral ridge drive punta gorda" — into clean, correctly-resolved parcels
# without the user hand-completing them, and stops the geocoder from wandering
# to a same-named street in another state (e.g. "1432 coral ridge dr" → NY).
_STATE_ABBRS = set(_STATE_NAMES.keys()) | {"FL"}
_STATE_FULLNAMES = {v.lower() for v in _STATE_NAMES.values()} | {"florida"}


def _has_state(s: str) -> bool:
    """True if the address already names a US state (full name, or a 2-letter
    code sitting in the state position — after a comma, or right before a ZIP)."""
    low = s.lower()
    for name in _STATE_FULLNAMES:
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return True
    # 2-letter code after a comma ("…, FL"), before a ZIP ("…FL 33950"), or as
    # the trailing token ("… atlanta ga"). Exclude NE/NW/SE/SW which are far more
    # likely a trailing street directional than Nebraska.
    _DIR2 = {"NE", "NW", "SE", "SW"}
    cands = re.findall(r",\s*([A-Za-z]{2})\b", s) + re.findall(r"\b([A-Za-z]{2})\.?\s+\d{5}\b", s)
    tail = re.search(r"\b([A-Za-z]{2})\s*$", s)
    if tail:
        cands.append(tail.group(1))
    for m in cands:
        u = m.upper()
        if u in _STATE_ABBRS and (u not in _DIR2 or f", {u}" in s.upper()):
            return True
    return False


def _clean_address(address: str) -> str:
    """Tidy raw user input before geocoding: collapse whitespace, trim stray
    trailing punctuation. Cheap, lossless, applied to every lookup."""
    s = re.sub(r"\s+", " ", (address or "").strip())
    return s.strip(" ,;\t")


def _needs_fl(s: str) -> bool:
    """We can safely bias to FL when the address carries neither a ZIP nor a
    state — those are the ones that mis-resolve out of state."""
    if re.search(r"\b\d{5}(?:-\d{4})?\b", s):
        return False
    return not _has_state(s)


def _with_fl(s: str) -> str:
    return s.rstrip(" ,;") + ", FL"


def _classify(candidates: list) -> dict:
    """Apply accuracy/type/state rules to a list of Geocodio candidates.

    Returns a dict that ALWAYS carries a "status" key:
      ok             -> lat, lng, formatted_address (+ accuracy metadata)
      wrong_state    -> state (full name), formatted_address
      low_confidence -> suggestions (list of formatted FL addresses, may be empty)
      not_found      -> nothing else
    """
    if not candidates:
        return {"status": "not_found"}

    def _state(c):
        return (c.get("address_components") or {}).get("state") or ""

    # First candidate that passes ALL checks wins
    for c in candidates:
        acc = c.get("accuracy") or 0
        acc_type = c.get("accuracy_type") or ""
        if acc >= MIN_ACCURACY and acc_type in ACCEPTED_ACCURACY_TYPES and _state(c) == "FL":
            loc = c.get("location") or {}
            return {
                "status": "ok",
                "lat": loc.get("lat"),
                "lng": loc.get("lng"),
                "formatted_address": c.get("formatted_address", ""),
                "accuracy": acc,
                "accuracy_type": acc_type,
            }

    # Confident match, but in another state entirely
    top = candidates[0]
    if (top.get("accuracy") or 0) >= MIN_ACCURACY \
            and (top.get("accuracy_type") or "") in ACCEPTED_ACCURACY_TYPES \
            and _state(top) and _state(top) != "FL":
        abbr = _state(top)
        return {
            "status": "wrong_state",
            "state": _STATE_NAMES.get(abbr, abbr),
            "formatted_address": top.get("formatted_address", ""),
        }

    # FL candidates that are street-level but below the confidence bar become
    # "did you mean" suggestions. (The FL DOR LIKE fallback in the original
    # design is infeasible — the statewide layer times out on attribute LIKE.)
    suggestions = []
    for c in candidates:
        acc = c.get("accuracy") or 0
        if _state(c) == "FL" and acc >= SUGGEST_MIN_ACCURACY \
                and (c.get("accuracy_type") or "") in ACCEPTED_ACCURACY_TYPES:
            fa = c.get("formatted_address", "")
            if fa and fa not in suggestions:
                suggestions.append(fa)
        if len(suggestions) == 3:
            break
    return {"status": "low_confidence", "suggestions": suggestions}


async def _geocode_raw(query: str) -> dict:
    """One Geocodio call + classification. No normalization."""
    try:
        api_key = os.environ['GEOCODIO_API_KEY']
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.geocod.io/v1.7/geocode",
                params={"q": query, "api_key": api_key, "limit": 5},
            )
            resp.raise_for_status()
            data = resp.json()
        return _classify(data.get("results", []))
    except Exception as e:
        print(f"[Geocodio] Error: {e}")
        return {"status": "not_found"}


async def geocode(address: str) -> dict:
    """Geocode one address, auto-completing sloppy input. Always returns a dict
    with a "status" key.

    We clean the text, try it as-is, and — for a Florida-only product — retry
    biased to FL when the input names no state and the first pass didn't land a
    confident FL match. That rescues partial addresses ("… punta gorda") and
    blocks wrong-state guesses without the user completing anything."""
    address = _clean_address(address)
    if not looks_like_address(address):
        return {"status": "invalid"}  # skip the paid lookup for obvious junk
    res = await _geocode_raw(address)
    if res.get("status") == "ok":
        return res
    if _needs_fl(address):
        res_fl = await _geocode_raw(_with_fl(address))
        if res_fl.get("status") == "ok":
            return res_fl
    return res


async def _batch_call(queries: list[str]) -> dict[str, dict]:
    """POST a list of query strings to Geocodio's batch endpoint (one call for
    up to 10,000). Returns {query: classified_dict}, one entry per query."""
    if not queries:
        return {}
    try:
        api_key = os.environ['GEOCODIO_API_KEY']
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.geocod.io/v1.7/geocode",
                params={"api_key": api_key, "limit": 5},
                json=queries,
            )
            resp.raise_for_status()
            data = resp.json()
        out: dict[str, dict] = {}
        for i, item in enumerate(data.get("results", [])):
            query = item.get("query") or (queries[i] if i < len(queries) else "")
            hits = item.get("response", {}).get("results", [])
            out[query] = _classify(hits)
        for q in queries:
            out.setdefault(q, {"status": "not_found"})
        return out
    except Exception as e:
        print(f"[Geocodio Batch] Error: {e}")
        return {q: {"status": "not_found"} for q in queries}


async def geocode_batch(addresses: list[str]) -> dict[str, dict]:
    """Geocode many addresses at once, auto-completing sloppy input.

    Returns {ORIGINAL_input_address: classified_dict} — every input keeps its
    exact original string as the key (callers look results up by it). Each
    address is cleaned, geocoded as-is, then a single Florida-biased retry batch
    rescues the ones that named no state and didn't land a confident FL match.
    At most two batch calls total, so speed stays flat even for big uploads."""
    if not addresses:
        return {}
    out: dict[str, dict] = {}
    cleaned: dict[str, str] = {}          # original -> cleaned
    for a in addresses:
        ca = _clean_address(a)
        if looks_like_address(ca):
            cleaned[a] = ca
        else:
            out[a] = {"status": "invalid"}
    if not cleaned:
        return out

    # Pass 1 — geocode the cleaned addresses (dedup identical queries).
    r1 = await _batch_call(list({ca for ca in cleaned.values()}))
    for a, ca in cleaned.items():
        out[a] = r1.get(ca, {"status": "not_found"})

    # Pass 2 — one FL-biased retry for the misses that can safely take FL.
    retry: dict[str, list[str]] = {}      # "<addr>, FL" -> [original addresses]
    for a, ca in cleaned.items():
        if out[a].get("status") != "ok" and _needs_fl(ca):
            retry.setdefault(_with_fl(ca), []).append(a)
    if retry:
        r2 = await _batch_call(list(retry.keys()))
        for fq, origs in retry.items():
            res = r2.get(fq)
            if res and res.get("status") == "ok":
                for a in origs:
                    out[a] = res
    return out
