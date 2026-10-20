# Landing copy tests

Current hero (control, **A**):
> FLORIDA BETA · LIVE DATA
> **Upload your leads. We'll kill the bad ones.**
> ParceliQ checks every parcel for flood zones, wetlands, protected wildlife, contamination records, road access, and county records. Your team makes faster, smarter offers.
> CTA: **Start screening free →**

## How to run these
There is no A/B tool wired in. Cheapest honest option: ship one variant at a time for ~2 weeks each and compare the weekly report numbers (signups per landing visit needs a visit counter — add Vercel Web Analytics (free tier) or count hash-route hits; that's a separate backlog item). With low traffic, read it as directional, not significant.
Primary metric: **landing → signup**. Secondary: **signup → first batch within 7 days** (activation, from the weekly report).

## Hero variants

**B — time saved (hypothesis: the pain is hours, not risk)**
> **30 minutes a parcel, or 30 seconds.**
> Upload your Florida vacant-land list. ParceliQ runs flood, wetlands, habitat, road access, soil and county-record checks on every lot at once and tells you which ones to pursue.
> CTA: **Screen my list free →**

**C — the mailer (hypothesis: wholesalers think in mail campaigns)**
> **Stop mailing swamp lots.**
> Run your list through 12 checks before it goes to print. Landlocked, flood zone, wetlands and protected habitat get flagged, with the reason, in minutes.
> CTA: **Clean my list free →**

**D — the outcome (hypothesis: show the answer, not the process)**
> **PURSUE or KILL. With the reason.**
> Every Florida parcel you upload gets a verdict, a 0–100 score and a plain-English explanation tied to the government source. No black box.
> CTA: **Get my first verdicts free →**

Recommendation: test **C** first. It names a concrete expensive mistake (postage on dead lots) and a clear action; the audience in priority #1 (wholesalers) is the biggest.

## Smaller tests (one at a time, after the hero)
| Element | Control | Variant | Why |
|---|---|---|---|
| Trust row under CTA | "Free during beta · No credit card required" | "Free during beta · No credit card · Your first 100 parcels in minutes" | gives a concrete first win |
| Secondary CTA | "▶ See how it works" | "See a real verdict ↓" (scrolls to Deal Review demo) | the demo is the most convincing block on the page |
| Stats strip | animated 0 → 67 / 10+ / 2 / 0 | static numbers (no count-up) | count-up shows "0" in screenshots and to crawlers/link previews |
| Final CTA headline | "Stop guessing. Start closing better deals." | "A 100-lead list is a week of evenings. Or one upload." | reuses the strongest line from the pricing page |
| Pricing link | "View pricing" | "Free in beta — see future pricing" | removes the fear that clicking means paying |

## Copy fixes worth shipping regardless (not tests)
- The demo verdict shows owner "📞 Phone — Coming Soon / ✉️ Email — Coming Soon": two "coming soon" labels in the best-selling block read as missing features. Consider hiding them on the landing demo.
- "Join land investors and acquisition teams using ParceliQ" implies a user base; fine once true, otherwise "Built for land investors and acquisition teams".
