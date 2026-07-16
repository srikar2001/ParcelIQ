"""
ParcelIQ background worker — standalone long-running process.

Deployed as a SECOND Railway service in the same project, alongside the
FastAPI web service. Polls the job_queue Supabase table every 5 seconds
for status='queued' rows and processes them one at a time.

Phase 1 / Session 1.2: this is the skeleton loop only. The actual
parcel-processing logic (Session 1.3) replaces the `_process_job`
placeholder below — everything around it (claim → work → complete, error
handling, polling) is the real machinery.

Runs entirely against the Supabase REST API (PostgREST) using the same
SUPABASE_URL / SUPABASE_KEY the web service already has — no dependency on
the app package, so it can't be affected by (or affect) the web service.

Start command (Railway worker service):  python worker.py
"""
from __future__ import annotations

import os
import sys
import time
import signal
from datetime import datetime, timezone

import httpx

try:
    from dotenv import load_dotenv  # local dev convenience; a no-op on Railway
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY", "")
)

POLL_INTERVAL_SECONDS = 5
PLACEHOLDER_WORK_SECONDS = 2
REST = f"{SUPABASE_URL}/rest/v1/job_queue"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

_running = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    """Timestamped, line-buffered logging so Railway shows it live."""
    print(f"[worker {_now()}] {msg}", flush=True)


def _claim_next_job(client: httpx.Client) -> dict | None:
    """
    Atomically claim the oldest queued job: flip queued -> processing with a
    filtered UPDATE so two workers can't grab the same row. Returns the
    claimed row, or None if nothing was queued.
    """
    # Find the oldest queued job.
    resp = client.get(
        REST,
        params={
            "status": "eq.queued",
            "select": "job_id,batch_id,total_parcels",
            "order": "created_at.asc",
            "limit": "1",
        },
        headers={**HEADERS, "Prefer": "return=representation"},
        timeout=20,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    job_id = rows[0]["job_id"]

    # Claim it — the status=eq.queued filter makes this a compare-and-set: if
    # another worker already flipped it, our UPDATE matches zero rows.
    claim = client.patch(
        REST,
        params={"job_id": f"eq.{job_id}", "status": "eq.queued"},
        json={"status": "processing", "started_at": _now()},
        headers={**HEADERS, "Prefer": "return=representation"},
        timeout=20,
    )
    claim.raise_for_status()
    claimed = claim.json()
    if not claimed:
        return None  # lost the race to another worker
    return claimed[0]


def _mark_complete(client: httpx.Client, job_id: str) -> None:
    client.patch(
        REST,
        params={"job_id": f"eq.{job_id}"},
        json={"status": "complete", "completed_at": _now()},
        headers=HEADERS,
        timeout=20,
    ).raise_for_status()


def _mark_error(client: httpx.Client, job_id: str, message: str) -> None:
    try:
        client.patch(
            REST,
            params={"job_id": f"eq.{job_id}"},
            json={"status": "error", "error_message": message[:1000], "completed_at": _now()},
            headers=HEADERS,
            timeout=20,
        ).raise_for_status()
    except Exception as exc:
        log(f"failed to mark job {job_id} as error: {exc}")


def _process_job(client: httpx.Client, job: dict) -> None:
    """
    PLACEHOLDER (Session 1.2). Real parcel processing arrives in Session 1.3.
    For now: log the job and sleep to simulate work.
    """
    job_id = job["job_id"]
    log(f"processing job {job_id} (batch_id={job.get('batch_id')}, "
        f"total_parcels={job.get('total_parcels')}) — placeholder work")
    time.sleep(PLACEHOLDER_WORK_SECONDS)


def _handle_signal(signum, _frame) -> None:
    global _running
    log(f"received signal {signum}, finishing current loop then exiting…")
    _running = False


def run() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        log("FATAL: SUPABASE_URL and SUPABASE_KEY must be set. Exiting.")
        sys.exit(1)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log(f"started. polling job_queue every {POLL_INTERVAL_SECONDS}s "
        f"at {SUPABASE_URL}")

    with httpx.Client() as client:
        while _running:
            try:
                job = _claim_next_job(client)
                if job is None:
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue
                job_id = job["job_id"]
                try:
                    _process_job(client, job)
                    _mark_complete(client, job_id)
                    log(f"completed job {job_id}")
                except Exception as exc:
                    log(f"job {job_id} failed: {exc}")
                    _mark_error(client, job_id, str(exc))
            except Exception as exc:
                # Never let a transient network/DB error kill the worker.
                log(f"poll loop error (will retry): {exc}")
                time.sleep(POLL_INTERVAL_SECONDS)

    log("stopped.")


if __name__ == "__main__":
    run()
