"""
Offline test for the offer helper (app/core/offer.py).
Run: backend/venv/bin/python tests/test_offer.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.offer import estimate_offer  # noqa: E402


def comp(ppa, ac, d=100, yr=2024):
    return {"price_per_acre": ppa, "acreage": ac, "sale_price": ppa * ac, "sale_year": yr,
            "distance_m": d, "address": f"{ppa}/{ac}"}


# 1) 7 similar-size comps around $10k/ac + one $200k/ac outlier + two tiny lots.
comps = [comp(p, 5, d=i * 50) for i, p in enumerate([8000, 9000, 9500, 10000, 10500, 11000, 12000])]
comps += [comp(200_000, 5, d=10), comp(60_000, 0.2), comp(70_000, 0.25)]
comps += [{"price_per_acre": None, "acreage": 0.1, "sale_price": 400000}]  # improved sale: ignored
r = estimate_offer(5, comps)
assert r["ok"] and r["basis"] == "comps", r
assert r["size_matched"] and r["comps_used"] == 7 and r["outliers_dropped"] == 1, r
assert r["price_per_acre"]["mid"] == 10000, r
assert r["market_value"]["mid"] == 50000, r
assert r["offer"] == {"low": 15000, "high": 25000}, r          # standard 30-50%
assert r["confidence"] == "high", r
assert r["comps"][0]["distance_m"] == 0, r                       # nearest first

# 2) presets and custom %
assert estimate_offer(5, comps, preset="aggressive")["offer"] == {"low": 25000, "high": 35000}
assert estimate_offer(5, comps, pct_low=0.4, pct_high=0.6)["offer"] == {"low": 20000, "high": 30000}
assert estimate_offer(5, comps, pct_low=0.9, pct_high=0.2)["offer"] == {"low": 15000, "high": 25000}  # invalid -> preset

# 3) too few similar-size comps -> falls back to all land comps, medium confidence
few = [comp(10000, 40), comp(12000, 50), comp(30000, 1), comp(35000, 1.5)]
r = estimate_offer(5, few)
assert r["ok"] and not r["size_matched"] and r["comps_used"] == 4 and r["confidence"] == "medium", r

# 4) <3 comps -> county land value anchor, low confidence
r = estimate_offer(2, [comp(5000, 2)], land_value=18000, just_value=25000)
assert r["ok"] and r["basis"] == "land_value" and r["confidence"] == "low", r
assert r["market_value"]["mid"] == 18000 and r["offer"] == {"low": 5500, "high": 9000}, r
assert estimate_offer(2, [], just_value=25000)["basis"] == "just_value"

# 5) failure paths
assert estimate_offer(None, comps)["reason"] == "no_acreage"
assert estimate_offer(0, comps)["reason"] == "no_acreage"
assert estimate_offer(3, [])["reason"] == "no_comps"
assert estimate_offer("abc", ["junk", 5, None])["reason"] == "no_acreage"
assert estimate_offer(3, ["junk", {"price_per_acre": "x"}], land_value="nan")["reason"] == "no_comps"

# 6) small offers round to $50, mid to $500, big to $1k
r = estimate_offer(0.25, [comp(20000, 0.25)] * 3)
assert r["offer"] == {"low": 1500, "high": 2500}, r
r = estimate_offer(100, [comp(9876, 100)] * 3)
assert r["offer"]["low"] % 1000 == 0, r

# 7) new-build (improved at sale) and multi-parcel sales are not land comps
dirty = [comp(10000, 1)] * 3 + [dict(comp(1_500_000, 0.2), improved_at_sale=True)] * 5 + [dict(comp(900_000, 1), multi_parcel=True)] * 5
r = estimate_offer(1, dirty)
assert r["comps_used"] == 3 and r["market_value"]["mid"] == 10000, r

# 8) DOR-qualified sales win when there are 3+
mix = [dict(comp(10000, 1), qualified=True)] * 3 + [comp(40000, 1)] * 6
r = estimate_offer(1, mix)
assert r["qualified_only"] and r["market_value"]["mid"] == 10000, r

# 9) size adjustment: a 10-ac sale at $5k/ac ($50k) implies ~$19k for 2 ac ($9.5k/ac), not a flat $10k
r = estimate_offer(2, [comp(5000, 10)] * 3)
assert 18500 < r["market_value"]["mid"] < 19500, r

print("test_offer: all passed")
