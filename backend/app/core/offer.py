"""Offer helper: a suggested offer range for a parcel from nearby land sales.

Pure computation over data the client already has (the subject's acreage and
county values plus the comps it fetched from /api/explore/comps), so it is
cheap and deterministic. Method, in the order a land investor would comp:

1. Keep land comps only: vacant/ag sales with a plausible $/acre, minus lots
   that sold already improved (DOR VI_CD1 'I') and multi-parcel sales.
2. Scale each sale to the subject's size (price x (acres/comp acres)^0.6,
   since $/acre falls with lot size). Prefer similar-size comps (1/3x-3x)
   and DOR-qualified arm's-length sales when there are at least 3.
3. Trim outliers with the 1.5x IQR rule, then take the 25th/50th/75th
   percentile as the low / likely / high market value.
4. Offer = a % of the likely market value (land investors typically open at
   25-50%: the margin pays for holding, closing, marketing and risk).

With fewer than 3 usable comps the county's land value (DOR LND_VAL, or just
value) is the anchor instead, and confidence is reported as low.
"""
from __future__ import annotations

from statistics import median
from typing import Optional

# Offer % of market value. "standard" is the default the UI opens on.
PRESETS = {
    "conservative": (0.25, 0.35),
    "standard": (0.30, 0.50),
    "aggressive": (0.50, 0.70),
}
_SIZE_BAND = 3.0       # similar = within 1/3x .. 3x the subject acres
_MIN_COMPS = 3
_MAX_PPA = 2_000_000   # same sanity cap as /api/explore/comps
# Land $/acre falls as lots get bigger: total price grows ~acres^0.6 in rural
# land sales, so a comp's price is scaled to the subject's size with that.
_SIZE_ELASTICITY = 0.6


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _pct(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _round_offer(v: float) -> int:
    """Round to a number that reads like a real offer ($50 / $500 / $1k steps)."""
    step = 50 if v < 5_000 else 500 if v < 50_000 else 1_000
    return int(round(v / step) * step)


def _land_comps(comps: list) -> list[dict]:
    out = []
    for c in comps or []:
        if not isinstance(c, dict):
            continue
        ppa, ac, price = _num(c.get("price_per_acre")), _num(c.get("acreage")), _num(c.get("sale_price"))
        if not ppa or ppa <= 0 or ppa > _MAX_PPA or not ac or ac <= 0 or not price:
            continue
        # Sold with a house on it / one price for several parcels: not a land comp.
        if c.get("improved_at_sale") is True or c.get("multi_parcel") is True:
            continue
        out.append({"ppa": ppa, "acreage": ac, "sale_price": price,
                    "sale_year": c.get("sale_year"), "address": c.get("address"),
                    "qualified": c.get("qualified") is True,
                    "distance_m": _num(c.get("distance_m"))})
    return out


def _size_adjusted(c: dict, acres: float) -> float:
    """What this comp's sale implies for a lot of the subject's size."""
    return c["sale_price"] * (acres / c["acreage"]) ** _SIZE_ELASTICITY


def _iqr_trim(pool: list[dict]) -> list[dict]:
    if len(pool) < 4:
        return pool
    vals = sorted(c["est"] for c in pool)
    q1, q3 = _pct(vals, 0.25), _pct(vals, 0.75)
    iqr = q3 - q1
    return [c for c in pool if q1 - 1.5 * iqr <= c["est"] <= q3 + 1.5 * iqr]


def estimate_offer(acreage, comps: list, land_value=None, just_value=None,
                   preset: str = "standard", pct_low=None, pct_high=None) -> dict:
    acres = _num(acreage)
    if not acres or acres <= 0:
        return {"ok": False, "reason": "no_acreage",
                "note": "Acreage is unknown for this parcel, so a per-acre offer can't be computed."}

    lo_pct, hi_pct = PRESETS.get(preset, PRESETS["standard"])
    pl, ph = _num(pct_low), _num(pct_high)
    if pl is not None and ph is not None and 0.05 <= pl <= ph <= 1.0:
        lo_pct, hi_pct = pl, ph

    land = _land_comps(comps)
    for c in land:
        c["est"] = _size_adjusted(c, acres)
    similar = [c for c in land if acres / _SIZE_BAND <= c["acreage"] <= acres * _SIZE_BAND]
    pool, size_matched = (similar, True) if len(similar) >= _MIN_COMPS else (land, False)
    # DOR-qualified (arm's-length) sales are the better evidence when there are enough.
    qual = [c for c in pool if c["qualified"]]
    if len(qual) >= _MIN_COMPS:
        pool = qual

    used = _iqr_trim(pool)
    dropped = len(pool) - len(used)

    if len(used) >= _MIN_COMPS:
        vals = sorted(c["est"] for c in used)
        mv_lo, mv_mid, mv_hi = _pct(vals, 0.25), median(vals), _pct(vals, 0.75)
        spread = (mv_hi - mv_lo) / mv_mid if mv_mid else 9
        conf = ("high" if len(used) >= 6 and size_matched and spread <= 0.6
                else "medium" if spread <= 1.2 else "low")
        basis = "comps"
    else:
        anchor = _num(land_value) or _num(just_value)
        if not anchor or anchor <= 0:
            return {"ok": False, "reason": "no_comps",
                    "comps_found": len(land),
                    "note": "Not enough nearby land sales and no county value on file to price an offer."}
        basis, conf, spread = ("land_value" if _num(land_value) else "just_value"), "low", None
        mv_lo, mv_mid, mv_hi = anchor * 0.8, anchor, anchor * 1.25
    ppa_lo, ppa_mid, ppa_hi = mv_lo / acres, mv_mid / acres, mv_hi / acres
    county = _num(land_value) or _num(just_value)

    used_sorted = sorted(used, key=lambda c: c["distance_m"] if c["distance_m"] is not None else 9e9)
    return {
        "ok": True,
        "basis": basis,
        "confidence": conf,
        "acreage": round(acres, 3),
        "comps_used": len(used) if basis == "comps" else 0,
        "qualified_only": basis == "comps" and all(c["qualified"] for c in used),
        "spread": round(spread, 2) if spread is not None else None,
        "county_value": round(county) if county else None,
        "comps_land": len(land),
        "outliers_dropped": dropped if basis == "comps" else 0,
        "size_matched": size_matched if basis == "comps" else False,
        "price_per_acre": {"low": round(ppa_lo), "mid": round(ppa_mid), "high": round(ppa_hi)},
        "market_value": {"low": round(mv_lo), "mid": round(mv_mid), "high": round(mv_hi)},
        "offer_pct": {"low": lo_pct, "high": hi_pct},
        "offer": {"low": _round_offer(mv_mid * lo_pct), "high": _round_offer(mv_mid * hi_pct)},
        "comps": [dict({k: c[k] for k in ("address", "acreage", "sale_price", "sale_year", "distance_m", "qualified")},
                        price_per_acre=round(c["ppa"]), adjusted_value=round(c["est"])) for c in used_sorted[:8]] if basis == "comps" else [],
    }
