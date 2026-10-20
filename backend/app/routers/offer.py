"""Offer helper: suggested offer range from nearby sales.

Pure computation over the comps the client already fetched (auth-gated
/api/explore/comps) plus the parcel's acreage and county values — no upstream
calls, no DB — so it is public like /api/checklist. Body is size-capped.
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.offer import estimate_offer

router = APIRouter(prefix="/api/offer")

_MAX_BODY = 100_000
_MAX_COMPS = 100


@router.post("")
async def offer_for_parcel(request: Request):
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        return JSONResponse({"error": "Request too large."}, status_code=413)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON."}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Expected an object."}, status_code=400)
    comps = body.get("comps") if isinstance(body.get("comps"), list) else []
    return estimate_offer(
        body.get("acreage"), comps[:_MAX_COMPS],
        land_value=body.get("land_value"), just_value=body.get("just_value"),
        preset=body.get("preset") if isinstance(body.get("preset"), str) else "standard",
        pct_low=body.get("pct_low"), pct_high=body.get("pct_high"),
    )
