-- Migration 001: job_queue
-- Phase 1 / Session 1.1 — durable job queue for large batch screening.
--
-- Replaces the in-memory background task (which dies on a Railway restart)
-- with a real table a separate worker process can poll and resume from.
--
-- Idempotent: safe to run more than once.

CREATE TABLE IF NOT EXISTS job_queue (
    job_id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id        text,
    status          text        NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued', 'processing', 'complete', 'error')),
    total_parcels   integer,
    processed_count integer     NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    completed_at    timestamptz,
    error_message   text
);

-- Fast polling: worker repeatedly asks "any queued/processing jobs?"
CREATE INDEX IF NOT EXISTS idx_job_queue_status ON job_queue (status);
