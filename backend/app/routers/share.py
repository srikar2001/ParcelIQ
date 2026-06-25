"""Share batch — generate public read-only links."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api")

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = (
    os.environ.get("SUPABASE_ANON_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY", "")
)
_FRONTEND_URL = "https://parcel-iq-nine.vercel.app"


class ShareRequest(BaseModel):
    batch_id: str


@router.post("/share/batch")
async def create_share(req: ShareRequest):
    if not _SUPABASE_URL:
        return JSONResponse(status_code=503, content={"error": "Database not configured"})
    token = str(uuid.uuid4()).replace("-", "")[:16]
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{_SUPABASE_URL}/rest/v1/shared_batches",
                json={
                    "batch_id": req.batch_id,
                    "token": token,
                    "expires_at": expires,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                headers={
                    "apikey": _SUPABASE_KEY,
                    "Authorization": f"Bearer {_SUPABASE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
            )
    except Exception as e:
        print(f"[Share] DB error: {e}")
        token = str(uuid.uuid4()).replace("-", "")[:16]

    return {"share_url": f"{_FRONTEND_URL}/#shared/{token}", "token": token, "expires_at": expires}


@router.get("/share/{token}")
async def get_shared(token: str):
    if not _SUPABASE_URL:
        return JSONResponse(status_code=503, content={"error": "Database not configured"})
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"{_SUPABASE_URL}/rest/v1/shared_batches",
                params={"token": f"eq.{token}", "select": "batch_id,expires_at", "limit": "1"},
                headers={"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}"},
            )
        rows = r.json()
        if not rows:
            return JSONResponse(status_code=404, content={"error": "Share link not found"})
        row = rows[0]
        expires = row.get("expires_at", "")
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return JSONResponse(status_code=404, content={"error": "Share link expired"})
        batch_id = row["batch_id"]
        # Load batch + results
        async with httpx.AsyncClient(timeout=8.0) as client:
            br = await client.get(
                f"{_SUPABASE_URL}/rest/v1/batches",
                params={"id": f"eq.{batch_id}", "select": "*", "limit": "1"},
                headers={"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}"},
            )
            pr = await client.get(
                f"{_SUPABASE_URL}/rest/v1/parcel_results",
                params={"batch_id": f"eq.{batch_id}", "select": "*"},
                headers={"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}"},
            )
        return {"batch": br.json()[0] if br.json() else {}, "results": pr.json() or []}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
