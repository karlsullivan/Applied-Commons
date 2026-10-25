-- Research on the production library's tasks (policy section 4l;
-- maintainer decision 2026-10-25): AC's research workers take open library
-- tasks at a small monthly quota (library-research jobs), and a person or
-- an assistant session can record the same kind of work by hand. Each run
-- is kept with its sourced findings. Findings are claims about a staged
-- record, append-only, with an evidence state: SUPPORTED needs a source,
-- and nothing here verifies a record or closes a task; that stays a human
-- review.

CREATE TABLE IF NOT EXISTS library_research (
    id BIGSERIAL PRIMARY KEY,
    task_key TEXT NOT NULL REFERENCES library_tasks(key) ON DELETE CASCADE,
    job_id BIGINT REFERENCES jobs(id),
    assessor TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    recommendation TEXT NOT NULL DEFAULT 'more-research'
        CHECK (recommendation IN ('evidence-sufficient', 'more-research', 'not-findable')),
    remaining_gaps JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS library_research_task ON library_research (task_key);

CREATE TABLE IF NOT EXISTS library_findings (
    id BIGSERIAL PRIMARY KEY,
    research_id BIGINT NOT NULL REFERENCES library_research(id) ON DELETE CASCADE,
    record_id BIGINT REFERENCES library_records(id) ON DELETE CASCADE,
    attribute TEXT NOT NULL,
    value JSONB,
    state TEXT NOT NULL CHECK (state IN ('SUPPORTED', 'INFERRED', 'UNKNOWN', 'CONFLICTED')),
    source_uri TEXT,
    locator TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (state <> 'SUPPORTED' OR source_uri IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS library_findings_record ON library_findings (record_id);
