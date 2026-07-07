"""Admin portal API — Sai only. Every endpoint verifies the caller's Supabase
token server-side and requires their email to be in _ADMIN_EMAILS; everyone
else gets a bare 403 with no data.

Schema notes (verified live): profiles has no is_admin/plan/limit columns and
DDL isn't possible through PostgREST, so:
  - admin identity = server-side email allowlist (equivalent security to an
    is_admin column: unforgeable token -> email -> allowlist)
  - per-user overrides (plan tier, daily limit, suspended) and waitlist
    invited-flags live as reserved keys in the existing `reports` KV table
    (address = 'admin:user:<uid>' / 'admin:waitlist:<id>')
Run `alter table profiles add column is_admin boolean default false;` etc. in
the Supabase dashboard later to migrate this properly.
"""
import json
import os
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.routers.batch import (
    _user_id_from_token, _AuthUnavailable,
    _SUPABASE_URL, _SUPABASE_SVC_KEY, _SB_HEADERS_SVC, WEEKLY_LIMIT,
)

router = APIRouter(prefix="/api/admin")

_ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "srikarvudaru1@gmail.com").split(",")
    if e.strip()
}

_FORBIDDEN = JSONResponse(status_code=403, content={"error": "Forbidden"})


async def _require_admin(authorization: Optional[str]):
    """Returns None if the caller is an admin, else a 403 response."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    try:
        id_email = await _user_id_from_token(token)
    except _AuthUnavailable:
        return _FORBIDDEN  # fail CLOSED for admin — unlike screening
    if not id_email or (id_email[1] or "").lower() not in _ADMIN_EMAILS:
        return _FORBIDDEN
    return None


async def _sb_get(path: str, params: dict | None = None, headers_extra: dict | None = None):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{_SUPABASE_URL}{path}", params=params or {},
                             headers={**_SB_HEADERS_SVC(), **(headers_extra or {})})
    return r


# ── Override storage: shared with batch.py (which enforces them) ─────────────
from app.routers.batch import get_user_override, _override_get_all, _bust_override_cache


async def _override_set(key: str, data: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{_SUPABASE_URL}/rest/v1/reports",
                json={"address": key, "folio": key, "report_json": json.dumps(data)},
                headers={**_SB_HEADERS_SVC(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False



# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("/check")
async def admin_check(authorization: Optional[str] = Header(None)):
    denied = await _require_admin(authorization)
    if denied is not None:
        return denied
    return {"admin": True}


@router.get("/users")
async def admin_users(authorization: Optional[str] = Header(None)):
    denied = await _require_admin(authorization)
    if denied is not None:
        return denied
    # Auth admin API (needs service_role); fall back to profiles
    users = []
    try:
        r = await _sb_get("/auth/v1/admin/users", {"per_page": "200"})
        if r.status_code == 200:
            payload = r.json()
            users = payload.get("users", payload if isinstance(payload, list) else [])
    except Exception:
        users = []
    if not users:
        r = await _sb_get("/rest/v1/profiles", {"select": "id,email,full_name,created_at", "limit": "500"})
        users = r.json() if r.status_code == 200 else []

    # Usage aggregates from batches
    r = await _sb_get("/rest/v1/batches", {"select": "user_id,total,created_at", "limit": "10000"})
    batches = r.json() if r.status_code == 200 else []
    from datetime import datetime, timezone
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    agg: dict = {}
    for b in batches:
        uid = b.get("user_id")
        if not uid:
            continue
        a = agg.setdefault(uid, {"all_time": 0, "today": 0, "batches": 0, "last_active": None})
        a["all_time"] += b.get("total") or 0
        a["batches"] += 1
        ca = b.get("created_at") or ""
        if ca >= day_start:
            a["today"] += b.get("total") or 0
        if not a["last_active"] or ca > a["last_active"]:
            a["last_active"] = ca

    overrides = await _override_get_all("admin:user:")
    out = []
    for u in users:
        uid = u.get("id")
        ov = overrides.get(uid, {})
        a = agg.get(uid, {})
        out.append({
            "id": uid,
            "email": u.get("email"),
            "created_at": u.get("created_at"),
            "last_sign_in_at": u.get("last_sign_in_at"),
            "plan": ov.get("plan", "free"),
            "daily_limit": ov.get("daily_limit", WEEKLY_LIMIT),
            "suspended": bool(ov.get("suspended")),
            "today_usage": a.get("today", 0),
            "all_time": a.get("all_time", 0),
            "batches": a.get("batches", 0),
            "last_active": a.get("last_active"),
        })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"users": out}


@router.get("/stats")
async def admin_stats(authorization: Optional[str] = Header(None)):
    denied = await _require_admin(authorization)
    if denied is not None:
        return denied
    r = await _sb_get("/rest/v1/batches", {"select": "user_id,total,kills,pursues,created_at", "limit": "10000"})
    batches = r.json() if r.status_code == 200 else []
    rp = await _sb_get("/rest/v1/profiles", {"select": "id,created_at", "limit": "5000"})
    profiles = rp.json() if rp.status_code == 200 else []
    rk = await _sb_get("/rest/v1/parcel_results", {
        "select": "auto_kill_reason", "verdict": "eq.KILL",
        "order": "created_at.desc", "limit": "2000",
    })
    kill_rows = rk.json() if rk.status_code == 200 else []

    import re as _re2
    cats = [("Wetlands", r"wetland"), ("Flood zone", r"flood"), ("Wildlife habitat", r"habitat|wildlife"),
            ("Conservation easement", r"easement"), ("Road access", r"road|landlock"), ("Public land", r"public")]
    reason_counts: dict = {}
    for row in kill_rows:
        akr = row.get("auto_kill_reason") or ""
        name = next((n for n, pat in cats if _re2.search(pat, akr, _re2.I)), "Stacked risk factors")
        reason_counts[name] = reason_counts.get(name, 0) + 1

    signups: dict = {}
    for p in profiles:
        d = (p.get("created_at") or "")[:10]
        if d:
            signups[d] = signups.get(d, 0) + 1

    return {
        "total_users": len(profiles),
        "total_parcels": sum(b.get("total") or 0 for b in batches),
        "total_batches": len(batches),
        "total_kills": sum(b.get("kills") or 0 for b in batches),
        "total_pursues": sum(b.get("pursues") or 0 for b in batches),
        "kill_reasons": sorted(reason_counts.items(), key=lambda kv: -kv[1]),
        "signups_by_day": sorted(signups.items()),
    }


@router.get("/waitlist")
async def admin_waitlist(authorization: Optional[str] = Header(None)):
    denied = await _require_admin(authorization)
    if denied is not None:
        return denied
    r = await _sb_get("/rest/v1/waitlist", {"select": "*", "order": "created_at.desc", "limit": "1000"})
    rows = r.json() if r.status_code == 200 else []
    invited = await _override_get_all("admin:waitlist:")
    for row in rows:
        row["invited"] = bool(invited.get(str(row.get("id")), {}).get("invited"))
    return {"waitlist": rows}


@router.get("/user/{user_id}/batches")
async def admin_user_batches(user_id: str, authorization: Optional[str] = Header(None)):
    denied = await _require_admin(authorization)
    if denied is not None:
        return denied
    r = await _sb_get("/rest/v1/batches", {
        "select": "id,name,total,kills,pursues,created_at",
        "user_id": f"eq.{user_id}", "order": "created_at.desc", "limit": "100",
    })
    return {"batches": r.json() if r.status_code == 200 else []}


class _UserPatch(BaseModel):
    plan: Optional[str] = None
    daily_limit: Optional[int] = None
    suspended: Optional[bool] = None


@router.post("/user/{user_id}/settings")
async def admin_user_settings(user_id: str, patch: _UserPatch, authorization: Optional[str] = Header(None)):
    denied = await _require_admin(authorization)
    if denied is not None:
        return denied
    current = await get_user_override(user_id)
    data = dict(current)
    if patch.plan is not None:
        data["plan"] = patch.plan
    if patch.daily_limit is not None:
        data["daily_limit"] = max(0, int(patch.daily_limit))
    if patch.suspended is not None:
        data["suspended"] = bool(patch.suspended)
    ok = await _override_set(f"admin:user:{user_id}", data)
    _bust_override_cache()
    if not ok:
        return JSONResponse(status_code=500, content={"error": "Failed to save override"})
    return {"ok": True, "settings": data}


class _InvitePatch(BaseModel):
    invited: bool = True


@router.post("/waitlist/{entry_id}/invited")
async def admin_waitlist_invited(entry_id: str, patch: _InvitePatch, authorization: Optional[str] = Header(None)):
    denied = await _require_admin(authorization)
    if denied is not None:
        return denied
    ok = await _override_set(f"admin:waitlist:{entry_id}", {"invited": patch.invited})
    if not ok:
        return JSONResponse(status_code=500, content={"error": "Failed to save"})
    # NOTE: no invite-email flow exists in the codebase yet — marking only.
    return {"ok": True, "invited": patch.invited, "email_sent": False,
            "note": "No invite email infrastructure exists yet — entry marked only."}


@router.delete("/user/{user_id}")
async def admin_delete_user(user_id: str, authorization: Optional[str] = Header(None)):
    denied = await _require_admin(authorization)
    if denied is not None:
        return denied
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # wipe user data rows first, then the auth user (needs service_role)
            for table, col in (("parcel_results", "user_id"), ("batches", "user_id"), ("profiles", "id")):
                await client.delete(f"{_SUPABASE_URL}/rest/v1/{table}",
                                    params={col: f"eq.{user_id}"}, headers=_SB_HEADERS_SVC())
            r = await client.delete(f"{_SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                                    headers=_SB_HEADERS_SVC())
        if r.status_code not in (200, 204):
            return JSONResponse(status_code=500, content={
                "error": "Data rows deleted, but the auth user could not be removed "
                         "(service_role key required on the server)."})
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
