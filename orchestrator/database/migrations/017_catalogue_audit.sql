-- Monthly catalogue audit and gap analysis (policy section 4j; maintainer
-- decision 2026-10-20).
--
-- A catalogue-audit job each month compares the catalogue with reference
-- lists of human needs and lists gaps. The maintainer decides each gap:
-- approved (act on it; a discover gap queues a focused candidate-discovery
-- job), rejected (ignore it) or proposed (open). A later audit closes the
-- gaps the catalogue now covers.
CREATE TABLE IF NOT EXISTS catalogue_audits (
    id BIGSERIAL PRIMARY KEY,
    month TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    job_id BIGINT REFERENCES jobs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_gaps (
    id BIGSERIAL PRIMARY KEY,
    audit_id BIGINT NOT NULL REFERENCES catalogue_audits(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    category_id BIGINT REFERENCES need_categories(id),
    category_name TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    action TEXT NOT NULL,
    focus TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected', 'closed')),
    decision_note TEXT,
    decided_at TIMESTAMPTZ,
    closed_by BIGINT REFERENCES catalogue_audits(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS audit_gaps_status ON audit_gaps (status);
