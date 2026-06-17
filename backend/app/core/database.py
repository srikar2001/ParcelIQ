"""
Supabase database client — report cache and search logging.

Tables required (create in Supabase SQL editor):
  reports  — cached full report JSON, keyed by folio, TTL 24h
  searches — search audit log
"""
from __future__ import annotations

from datetime import datetime, timedelta

from supabase import create_client, Client

from app.core.config import settings

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        if not settings.supabase_url or not settings.supabase_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
        _client = create_client(settings.supabase_url, settings.supabase_key)
    return _client


async def get_cached_report(folio: str) -> dict | None:
    try:
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        result = (
            _get_client()
            .table("reports")
            .select("*")
            .eq("folio", folio)
            .gt("updated_at", cutoff)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]
        return None
    except Exception as exc:
        print(f"[DB] Cache read error: {exc}")
        return None


async def cache_report(
    folio: str,
    address: str,
    report_json: dict,
    narrative: str,
    score: int,
) -> None:
    try:
        data = {
            "folio": folio,
            "address": address,
            "report_json": report_json,
            "narrative": narrative,
            "buildability_score": score,
            "updated_at": datetime.now().isoformat(),
        }
        existing = (
            _get_client()
            .table("reports")
            .select("id")
            .eq("folio", folio)
            .execute()
        )
        if existing.data:
            _get_client().table("reports").update(data).eq("folio", folio).execute()
        else:
            _get_client().table("reports").insert(data).execute()
    except Exception as exc:
        print(f"[DB] Cache write error: {exc}")


async def log_search(query: str, folio: str, address: str) -> None:
    try:
        _get_client().table("searches").insert(
            {"query": query, "folio": folio, "address": address}
        ).execute()
    except Exception as exc:
        print(f"[DB] Search log error: {exc}")
