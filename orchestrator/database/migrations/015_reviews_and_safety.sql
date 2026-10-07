-- Standards kept current, and build packs reviewed for safety (policy
-- sections 4e and 4i; maintainer decision 2026-10-07).
--
-- A monthly standards-review checks each approved standard against what
-- has been found since: adoption (counted from the records), conflicts,
-- and a recommendation (keep, revise, retire). A revision is a new
-- proposed standard that supersedes the old one once the maintainer
-- approves it. Approved standards are also re-verified yearly against
-- current editions of the established standards.
ALTER TABLE standards
    ADD COLUMN IF NOT EXISTS supersedes TEXT;

ALTER TABLE standards DROP CONSTRAINT IF EXISTS standards_status_check;
ALTER TABLE standards ADD CONSTRAINT standards_status_check
    CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded'));

CREATE TABLE IF NOT EXISTS standard_reviews (
    id BIGSERIAL PRIMARY KEY,
    standard_id BIGINT NOT NULL REFERENCES standards(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    adoption JSONB NOT NULL DEFAULT '{}'::jsonb,
    conflicts JSONB NOT NULL DEFAULT '[]'::jsonb,
    recommendation TEXT NOT NULL CHECK (recommendation IN ('keep', 'revise', 'retire')),
    rationale TEXT NOT NULL,
    revision TEXT,
    job_id BIGINT REFERENCES jobs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS standard_reviews_latest ON standard_reviews (standard_id, created_at DESC);

-- A safety review of a completed build pack: its hazards, the standards
-- that apply, the controls needed, and a verdict. The gate before
-- anything is built at the proving ground (stage 3).
CREATE TABLE IF NOT EXISTS safety_reviews (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL
        CHECK (verdict IN ('ok', 'controls-needed', 'qualified-person', 'do-not-build')),
    hazards JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    job_id BIGINT REFERENCES jobs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS safety_reviews_latest ON safety_reviews (project_id, created_at DESC);
