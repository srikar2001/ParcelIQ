"""Share batch — generate public read-only links."""
import os
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.collectors.parcel_fl import get_parcel_geometry_by_id

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
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail=f"Failed to save share link: {resp.text}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Share] DB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save share link — database error")

    return {"share_url": f"{_FRONTEND_URL}/#shared/{token}", "token": token, "expires_at": expires}


class ParcelShareRequest(BaseModel):
    result: dict


@router.post("/share/parcel")
async def create_parcel_share(req: ParcelShareRequest):
    """Snapshot a single parcel's Deal Review into a public read-only link.
    Stored in the reports KV table under a reserved 'shareparcel:<token>' key
    (same pattern the contact form uses) — no new table needed."""
    if not _SUPABASE_URL:
        return JSONResponse(status_code=503, content={"error": "Database not configured"})
    if not isinstance(req.result, dict) or not req.result:
        return JSONResponse(status_code=400, content={"error": "No parcel data to share"})
    token = str(uuid.uuid4()).replace("-", "")[:16]
    key = f"shareparcel:{token}"
    payload = {
        "address": key,
        "folio": key,
        "report_json": json.dumps({
            "created_at": datetime.now(timezone.utc).isoformat(),
            "result": req.result,
        }),
    }
    headers = {
        "apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(f"{_SUPABASE_URL}/rest/v1/reports", json=payload, headers=headers)
        if resp.status_code not in (200, 201, 204):
            raise HTTPException(status_code=500, detail=f"Failed to save share link: {resp.text}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Share] parcel DB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save share link — database error")
    return {"share_url": f"{_FRONTEND_URL}/#p/{token}", "token": token}


@router.get("/share/parcel/{token}")
async def get_parcel_share(token: str):
    if not _SUPABASE_URL:
        return JSONResponse(status_code=503, content={"error": "Database not configured"})
    key = f"shareparcel:{token}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"{_SUPABASE_URL}/rest/v1/reports",
                params={"address": f"eq.{key}", "select": "report_json", "limit": "1"},
                headers={"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}"},
            )
        rows = r.json()
        if not rows:
            return JSONResponse(status_code=404, content={"error": "Share link not found"})
        raw = rows[0].get("report_json")
        data = json.loads(raw) if isinstance(raw, str) else raw
        result = data.get("result") if isinstance(data, dict) else None
        if not result:
            return JSONResponse(status_code=404, content={"error": "Share link not found"})
        # Backfill geometry + center for older snapshots saved without them, so
        # the shared Deal Review renders the REAL map (by APN, free — no geocode).
        try:
            pi = result.get("parcel_info") or {}
            has_geom = bool(pi.get("geometry"))
            has_latlng = result.get("_lat") is not None and result.get("_lng") is not None
            if not has_geom and not has_latlng and pi.get("parcel_id"):
                geo = await get_parcel_geometry_by_id(pi.get("parcel_id"))
                if geo:
                    if geo.get("geometry"):
                        pi["geometry"] = geo["geometry"]
                        result["parcel_info"] = pi
                    result["_lat"] = geo["lat"]
                    result["_lng"] = geo["lng"]
        except Exception as enrich_err:
            print(f"[share] geometry backfill failed: {enrich_err}")
        return {"result": result}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


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


# ── Public contact form ──────────────────────────────────────────────────────
from datetime import datetime as _dt

_SUPABASE_SVC = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY")
    or os.environ.get("SUPABASE_KEY", "")
)

_CONTACT_SUBJECTS = {"General question", "Bug report", "Beta access", "Partnership", "Other"}


class ContactRequest(BaseModel):
    name: str
    email: str
    subject: str
    message: str


@router.post("/contact")
async def contact(req: ContactRequest):
    name = (req.name or "").strip()[:120]
    email = (req.email or "").strip()[:200]
    subject = req.subject if req.subject in _CONTACT_SUBJECTS else "Other"
    message = (req.message or "").strip()[:5000]
    if not name or not message or "@" not in email or "." not in email.split("@")[-1]:
        return JSONResponse(status_code=400, content={"error": "Please fill in your name, a valid email, and a message."})

    now = _dt.now(timezone.utc).isoformat()
    row = {"name": name, "email": email, "subject": subject, "message": message, "created_at": now}
    headers = {"apikey": _SUPABASE_SVC, "Authorization": f"Bearer {_SUPABASE_SVC}",
               "Content-Type": "application/json", "Prefer": "return=minimal"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"{_SUPABASE_URL}/rest/v1/contact_submissions", json=row, headers=headers)
            if r.status_code in (200, 201, 204):
                return {"ok": True}
            # Table may not exist yet. Never drop a lead: park it in the
            # reports KV table under a reserved key until the table is created
            # (SQL: create table contact_submissions (id uuid primary key
            # default gen_random_uuid(), name text, email text, subject text,
            # message text, created_at timestamptz default now());)
            kv = {"address": f"contact:{now}", "folio": f"contact:{now}", "report_json": json.dumps(row)}
            r2 = await client.post(f"{_SUPABASE_URL}/rest/v1/reports", json=kv,
                                   headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"})
            if r2.status_code in (200, 201, 204):
                return {"ok": True}
    except Exception as e:
        print(f"[Contact] save failed: {e}")
    return JSONResponse(status_code=500, content={"error": "Could not save your message."})
