"""
Search endpoint: accepts address, folio, STRAP, or PIN and returns
candidate parcels for selection.
"""
from fastapi import APIRouter, HTTPException, Query

from app.collectors import parcel
from app.models.schema import SearchResponse

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search(q: str = Query(..., min_length=2, description="Address, folio, STRAP, or PIN")):
    try:
        candidates = await parcel.search_parcels(q)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Parcel search failed: {exc}") from exc

    return SearchResponse(query=q, candidates=candidates)
