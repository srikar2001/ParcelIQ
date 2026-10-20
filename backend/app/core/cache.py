"""24-hour parcel result cache backed by Supabase reports table."""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.core.http import pooled_client

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
def _addr_key(address: str) -> str:
    # "1305 Grape Ave,  Interlachen" and "1305 grape ave, interlachen" are the
    # same parcel — collapse case + whitespace so they share one cache row.
    return re.sub(r"\s+", " ", address.strip().lower()).replace(" ,", ",")


_SUPABASE_KEY = (
    os.environ.get("SUPABASE_ANON_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY", "")
)


async def get_cached_result(address: str) -> dict | None:
    if not _SUPABASE_URL:
        return None
    try:
        # No TTL — a cached result is reused forever (repeats are always free /
        # instant). A re-screen sets skip_cache to force fresh data instead.
        addr_key = _addr_key(address)
        async with pooled_client(timeout=5.0) as client:
            resp = await client.get(
                f"{_SUPABASE_URL}/rest/v1/reports",
                params={
                    "select": "report_json,updated_at",
                    "address": f"eq.{addr_key}",
                    "order": "updated_at.desc",
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
        addr_key = _addr_key(address)
        payload = {
            "address": addr_key,
            "folio": addr_key,
            "report_json": json.dumps(result) if isinstance(result, dict) else result,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        async with pooled_client(timeout=5.0) as client:
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
