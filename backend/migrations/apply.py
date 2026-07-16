#!/usr/bin/env python3
"""
Standalone migration runner (not part of the app package).

Applies a .sql file to the Supabase Postgres database. DDL (CREATE TABLE,
CREATE INDEX, ...) cannot go through the PostgREST/publishable-key API, so
this needs one of two privileged credentials, checked in this order:

  1. SUPABASE_ACCESS_TOKEN  — a Supabase personal access token (starts with
     "sbp_"). Runs SQL via the Management API. Needs only httpx (already a
     dependency). Preferred: no extra install, works for arbitrary SQL.
     Get one at: https://supabase.com/dashboard/account/tokens

  2. SUPABASE_DB_URL        — a direct Postgres connection string, e.g.
     postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
     (or the pooler URL). Requires psycopg ("pip install psycopg[binary]").

Usage:
  SUPABASE_ACCESS_TOKEN=sbp_xxx python backend/migrations/apply.py backend/migrations/001_job_queue.sql
  SUPABASE_DB_URL=postgresql://... python backend/migrations/apply.py backend/migrations/001_job_queue.sql

The project ref is read from SUPABASE_URL in backend/.env (or the env).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _load_env() -> None:
    """Best-effort load of backend/.env so SUPABASE_URL etc. are available."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _project_ref() -> str | None:
    url = os.environ.get("SUPABASE_URL", "")
    m = re.search(r"https://([a-z0-9]+)\.supabase\.co", url)
    return m.group(1) if m else None


def _apply_via_management_api(sql: str, token: str) -> None:
    import httpx

    ref = _project_ref()
    if not ref:
        raise SystemExit("Could not determine project ref from SUPABASE_URL.")
    resp = httpx.post(
        f"https://api.supabase.com/v1/projects/{ref}/database/query",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": sql},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"Management API error {resp.status_code}: {resp.text}")
    print(f"[apply] OK via Management API (project {ref}).")


def _apply_via_postgres(sql: str, db_url: str) -> None:
    try:
        import psycopg
    except ImportError:
        raise SystemExit(
            "SUPABASE_DB_URL is set but psycopg is not installed.\n"
            "Install it with:  pip install 'psycopg[binary]'"
        )
    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    print("[apply] OK via direct Postgres connection.")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply.py <path-to.sql>")
    sql = Path(sys.argv[1]).read_text()
    _load_env()

    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    db_url = os.environ.get("SUPABASE_DB_URL", "").strip()

    if token:
        _apply_via_management_api(sql, token)
    elif db_url:
        _apply_via_postgres(sql, db_url)
    else:
        raise SystemExit(
            "No privileged credential found. Set one of:\n"
            "  SUPABASE_ACCESS_TOKEN  (personal access token, sbp_...)  — preferred\n"
            "  SUPABASE_DB_URL        (direct Postgres connection string)\n"
            "The publishable/anon SUPABASE_KEY cannot run DDL."
        )


if __name__ == "__main__":
    main()
