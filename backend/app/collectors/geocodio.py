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


async def geocode(address: str) -> dict:
    """Geocode one address. Always returns a dict with a "status" key."""
    if not looks_like_address(address):
        return {"status": "invalid"}  # skip the paid lookup for obvious junk
    try:
        api_key = os.environ['GEOCODIO_API_KEY']
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://api.geocod.io/v1.7/geocode",
                params={"q": address, "api_key": api_key, "limit": 5},
            )
            resp.raise_for_status()
            data = resp.json()
        return _classify(data.get("results", []))
    except Exception as e:
        print(f"[Geocodio] Error: {e}")
        return {"status": "not_found"}


async def geocode_batch(addresses: list[str]) -> dict[str, dict]:
    """Geocode up to 10,000 addresses in a single API call.

    Returns {input_address: classified_dict} — every input address gets an
    entry with a "status" key (ok / wrong_state / low_confidence / not_found).
    """
    if not addresses:
        return {}
    # Split off obvious junk locally so it never costs an API credit — only
    # plausible addresses are sent to the paid batch endpoint.
    valid: list[str] = []
    out: dict[str, dict] = {}
    for a in addresses:
        if looks_like_address(a):
            valid.append(a)
        else:
            out[a] = {"status": "invalid"}
    if not valid:
        return out
    try:
        api_key = os.environ['GEOCODIO_API_KEY']
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.geocod.io/v1.7/geocode",
                params={"api_key": api_key, "limit": 5},
                json=valid,
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        for i, item in enumerate(results):
            # Geocodio echoes the query string; fall back to positional order
            # (batch responses preserve input order) if it ever normalizes it.
            query = item.get("query") or (valid[i] if i < len(valid) else "")
            hits = item.get("response", {}).get("results", [])
            out[query] = _classify(hits)
        return out
    except Exception as e:
        print(f"[Geocodio Batch] Error: {e}")
        # Keep the locally-flagged invalids; mark the plausible ones not_found.
        for a in valid:
            out.setdefault(a, {"status": "not_found"})
        return out
