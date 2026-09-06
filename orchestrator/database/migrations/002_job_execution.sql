ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS priority DOUBLE PRECISION NOT NULL DEFAULT 0.5
        CHECK (priority >= 0 AND priority <= 1),
    ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0
        CHECK (attempts >= 0),
    ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS error TEXT;

CREATE INDEX IF NOT EXISTS idx_jobs_queue
    ON jobs (status, priority DESC, created_at);
