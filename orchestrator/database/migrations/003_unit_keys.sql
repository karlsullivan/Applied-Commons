-- Stable unit keys for discovered work: at most one job per unit of work,
-- so repeated discovery never enqueues the same unit twice.
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS unit_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_unit_key
    ON jobs (unit_key)
    WHERE unit_key IS NOT NULL;
