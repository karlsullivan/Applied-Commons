-- Source checks (ops/checks/check_sources.py; policy section 4g): whether
-- a project's links answer, its licence (from the repository host where
-- possible) and how active it is. Each check is a new row; the latest
-- one counts. licence_class decides what modules may build on:
-- open or share-alike only.
CREATE TABLE IF NOT EXISTS source_checks (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    links JSONB NOT NULL DEFAULT '[]'::jsonb,
    licence TEXT,
    licence_class TEXT NOT NULL
        CHECK (licence_class IN ('open', 'share-alike', 'restricted', 'none', 'unknown')),
    licence_source TEXT,
    repository TEXT,
    last_activity TIMESTAMPTZ,
    archived BOOLEAN,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS source_checks_latest ON source_checks (project_id, checked_at DESC);
