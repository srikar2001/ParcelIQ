"""Buildable acreage for a parcel boundary (wetlands + SFHA flood removed).

Pure local computation over the geostore (no upstream calls, no DB), so it is
public like the shared Deal Review that also shows it. Body and vertex counts
are capped.
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.buildable import compute

router = APIRouter(prefix="/api/buildable")

_MAX_BODY = 600_000


@router.post("")
async def buildable_for_parcel(request: Request):
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        return JSONResponse({"error": "Request too large."}, status_code=413)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON."}, status_code=400)
    rings = body.get("geometry") if isinstance(body, dict) else None
    if not isinstance(rings, list):
        return JSONResponse({"error": "Expected {geometry: [[[lat, lng], ...], ...]}."}, status_code=400)
    try:
        res = await compute(rings)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=413)
    if not res.get("ok") and res.get("reason") == "unavailable":
        return JSONResponse(res, status_code=503)
    return res
