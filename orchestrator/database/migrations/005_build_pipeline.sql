-- Need briefs, go/no-go reviews, build packs, design archives and
-- maintainer steering (project-selection policy sections 4b to 4d,
-- maintainer decision 2026-10-06).

-- One brief per category per refresh; the latest one is used.
CREATE TABLE IF NOT EXISTS need_briefs (
    id BIGSERIAL PRIMARY KEY,
    category_id BIGINT NOT NULL REFERENCES need_categories(id),
    job_id BIGINT REFERENCES jobs(id),
    brief JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_need_briefs_category
    ON need_briefs (category_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reviews (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id),
    job_id BIGINT REFERENCES jobs(id),
    review_number INTEGER NOT NULL,
    recommended TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('build', 'continue', 'park')),
    scores JSONB NOT NULL,
    composite DOUBLE PRECISION NOT NULL,
    fit TEXT NOT NULL,
    rationale TEXT NOT NULL,
    gaps JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per completed build pack section; the latest one is used.
CREATE TABLE IF NOT EXISTS build_packs (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id),
    job_id BIGINT REFERENCES jobs(id),
    section TEXT NOT NULL CHECK (section IN ('bom', 'design', 'assembly', 'test')),
    content JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_build_packs_project
    ON build_packs (project_id, section, created_at DESC);

-- Pinned local copies of design files (ops/archive/archive-designs.py).
CREATE TABLE IF NOT EXISTS design_archives (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id),
    source_uri TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('git', 'file')),
    revision TEXT NOT NULL DEFAULT '',       -- as requested by the build pack
    resolved_revision TEXT,                  -- the commit actually archived
    licence TEXT NOT NULL DEFAULT 'unknown',
    status TEXT NOT NULL CHECK (status IN ('archived', 'skipped', 'failed')),
    path TEXT,
    bytes BIGINT,
    sha256 TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_design_archives_source
    ON design_archives (project_id, source_uri, revision);

-- Maintainer override: 'build' forces a build pack (admitting a
-- candidate if needed), 'park' parks the project.
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS steer TEXT CHECK (steer IN ('build', 'park')),
    ADD COLUMN IF NOT EXISTS steer_note TEXT,
    ADD COLUMN IF NOT EXISTS steered_at TIMESTAMPTZ;

-- Questions are now marked answered when their literature job completes.
-- Mark the ones whose research already completed before that change.
UPDATE questions q SET status = 'answered'
WHERE q.status = 'open'
  AND EXISTS (SELECT 1 FROM jobs j
              WHERE j.unit_key = 'literature-collection:question:' || q.id
                AND j.status = 'completed');
