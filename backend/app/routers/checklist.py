"""Due-diligence checklist: county offices + parcel-specific questions.

Pure computation over the screening result the client already has (no
upstream calls, no DB), so it is public like the shared Deal Review that
also shows it. Geometry is dropped server-side; the body is size-capped.
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.county_dd import build_checklist, county_offices, normalize_county

router = APIRouter(prefix="/api/checklist")

_MAX_BODY = 200_000


@router.post("")
async def checklist_for_parcel(request: Request):
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        return JSONResponse({"error": "Request too large."}, status_code=413)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON."}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Expected a screening result object."}, status_code=400)
    pi = body.get("parcel_info") if isinstance(body.get("parcel_info"), dict) else {}
    result = {
        "address": body.get("address") if isinstance(body.get("address"), str) else None,
        "verdict": body.get("verdict"),
        "flags": [f for f in (body.get("flags") or []) if isinstance(f, str)][:60],
        "positives": [p for p in (body.get("positives") or []) if isinstance(p, str)][:60],
        "parcel_info": {k: v for k, v in pi.items() if k != "geometry"},
    }
    return build_checklist(result)


@router.get("/county/{name}")
async def offices_for_county(name: str):
    county = normalize_county(name)
    if not county:
        return JSONResponse({"error": "Unknown Florida county."}, status_code=404)
    return {"county": county, "offices": county_offices(county)}
