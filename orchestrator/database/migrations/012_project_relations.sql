-- Relations between catalogue projects (project-relations job, policy
-- section 4f): one project uses another (a house design cut on a CNC
-- router uses the router), enables it, is an alternative to it, or is
-- part of it. The wiki links related projects across categories with them.
-- An alternative is stored once, with a < b.
CREATE TABLE IF NOT EXISTS project_relations (
    id BIGSERIAL PRIMARY KEY,
    a BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    b BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('uses', 'enables', 'alternative', 'part-of')),
    why TEXT NOT NULL,
    job_id BIGINT REFERENCES jobs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (a <> b),
    UNIQUE (a, b, kind)
);

CREATE INDEX IF NOT EXISTS project_relations_b ON project_relations (b);
