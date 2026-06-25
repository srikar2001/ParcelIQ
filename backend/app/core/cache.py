"""24-hour parcel result cache backed by Supabase reports table."""
import json
import os
from datetime import datetime, timedelta, timezone

import httpx

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


async def get_cached_result(address: str) -> dict | None:
    if not _SUPABASE_URL:
        return None
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        addr_key = address.strip().lower()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{_SUPABASE_URL}/rest/v1/reports",
                params={
                    "select": "report_json,updated_at",
                    "address": f"eq.{addr_key}",
                    "updated_at": f"gte.{cutoff}",
                    "limit": "1",
                },
                headers={"apikey": _SUPABASE_KEY, "Authorization": f"Bearer {_SUPABASE_KEY}"},
            )
        if resp.status_code == 200 and resp.json():
            row = resp.json()[0]
            raw = row.get("report_json")
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str):
                return json.loads(raw)
        return None
    except Exception as e:
        print(f"[Cache] Get error: {e}")
        return None


async def save_cached_result(address: str, result: dict) -> None:
    if not _SUPABASE_URL:
        return
    try:
        payload = {
            "address": address.strip().lower(),
            "report_json": json.dumps(result) if isinstance(result, dict) else result,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{_SUPABASE_URL}/rest/v1/reports",
                json=payload,
                headers={
                    "apikey": _SUPABASE_KEY,
                    "Authorization": f"Bearer {_SUPABASE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
            )
    except Exception as e:
        print(f"[Cache] Save error: {e}")
