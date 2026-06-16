"""
Search endpoints: address/folio/STRAP/PIN search and real-time type-ahead suggest.
"""
from fastapi import APIRouter, HTTPException, Query

from app.collectors import parcel
from app.collectors.suggest import suggest_addresses
from app.models.schema import SearchResponse, SuggestResponse

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search(q: str = Query(..., min_length=2, description="Address, folio, STRAP, or PIN")):
    try:
        candidates = await parcel.search_parcels(q)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Parcel search failed: {exc}") from exc

    return SearchResponse(query=q, candidates=candidates)


@router.get("/suggest", response_model=SuggestResponse)
async def suggest(q: str = Query(..., min_length=2, description="Partial address for type-ahead")):
    try:
        results = await suggest_addresses(q)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Suggest failed: {exc}") from exc

    return SuggestResponse(
        query=q,
        results=[
            {"full_address": r.full_address, "folio": r.folio, "strap": r.strap,
             "city": r.city, "zip": r.zip, "lat": r.lat, "lon": r.lon}
            for r in results
        ],
    )
