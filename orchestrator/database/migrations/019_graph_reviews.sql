-- Independent review of safety-critical and medical graph candidates
-- (policy section 4k; maintainer decision 2026-10-21). A graph-review job
-- by the AC research worker checks each such candidate's claims against
-- sources it retrieves itself, at the revision it reviewed. An approval of
-- that revision names the review as its independent check; its findings
-- with sources are also recorded as assessments.
CREATE TABLE IF NOT EXISTS graph_reviews (
    id BIGSERIAL PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('need', 'capability', 'implementation')),
    subject_id BIGINT NOT NULL,
    revision BIGINT NOT NULL,
    job_id BIGINT REFERENCES jobs(id),
    reviewer TEXT NOT NULL,
    recommendation TEXT NOT NULL CHECK (recommendation IN ('approve', 'revise', 'reject')),
    summary TEXT NOT NULL,
    findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_hazards JSONB NOT NULL DEFAULT '[]'::jsonb,
    unchecked JSONB NOT NULL DEFAULT '[]'::jsonb,
    supported_maturity_min SMALLINT CHECK (supported_maturity_min BETWEEN 0 AND 5),
    supported_maturity_max SMALLINT CHECK (supported_maturity_max BETWEEN 0 AND 5),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS graph_reviews_subject
    ON graph_reviews (subject_type, subject_id, created_at DESC);
