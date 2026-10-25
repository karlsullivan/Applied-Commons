-- Production library (maintainer instruction 2026-10-25; policy section
-- 4l). An outside package (first: Applied Commons Production Library v0.4)
-- maps the means of production for a small community farm and workshop:
-- equipment, materials, process routes, designs, enterprises, shared
-- modules, external dependencies and eight local service systems.
--
-- It is staged beside the canonical records, never into them: the
-- Maslow -> category -> need -> capability graph, its evidence states and
-- B0-B7 levels, and the project catalogue are not written by an import.
-- Linking a staged record to a canonical one is a review decision.
--
-- - library_records: one row per staged record, keyed by its stable
--   external_key (origin:collection:id). A re-import with the same payload
--   hash changes nothing; a changed seed keeps the old payload in
--   library_record_versions and opens a review task. A record a human has
--   reviewed or verified is protected: a seed never overwrites it, and the
--   change waits as a conflict task. Records missing from a later package
--   are kept and flagged, never deleted.
-- - Requirement expressions (all_of / any_of) stay inside the payload,
--   intact: they are authoritative. library_edges is a navigational
--   projection only and is never read as an AND dependency list.
-- - library_reviews: human decisions, append-only; imports never touch them.
-- - library_links: proposed and confirmed links to canonical AC records
--   (a catalogue project, a graph capability or source).
-- - library_tasks: actionable research and review tasks, keyed so that a
--   re-import does not duplicate them or reopen a closed one.

CREATE TABLE IF NOT EXISTS library_imports (
    id BIGSERIAL PRIMARY KEY,
    origin TEXT NOT NULL,
    version TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    imported_by TEXT NOT NULL,
    report JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS library_records (
    id BIGSERIAL PRIMARY KEY,
    external_key TEXT UNIQUE NOT NULL,
    origin TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    -- staged: a seed as imported. reviewed / verified: a human has looked
    -- (verified needs evidence); both are protected from seeds. rejected:
    -- kept, not shown as a candidate.
    review_status TEXT NOT NULL DEFAULT 'staged'
        CHECK (review_status IN ('staged', 'reviewed', 'verified', 'rejected')),
    in_latest_import BOOLEAN NOT NULL DEFAULT true,
    first_import_id BIGINT NOT NULL REFERENCES library_imports(id),
    last_import_id BIGINT NOT NULL REFERENCES library_imports(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (origin, entity_type, record_id)
);
CREATE INDEX IF NOT EXISTS library_records_type ON library_records (entity_type);

CREATE TABLE IF NOT EXISTS library_record_versions (
    id BIGSERIAL PRIMARY KEY,
    record_id BIGINT NOT NULL REFERENCES library_records(id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    import_id BIGINT NOT NULL REFERENCES library_imports(id),
    replaced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS library_edges (
    id BIGSERIAL PRIMARY KEY,
    origin TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('projection', 'legacy-candidate-support')),
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    logic TEXT NOT NULL DEFAULT '',
    group_record TEXT NOT NULL DEFAULT '',
    -- The package's own evidence label, and the AC evidence state it maps
    -- to: a seed or editorial mapping is INFERRED at best, never SUPPORTED.
    evidence_original TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'INFERRED'
        CHECK (state IN ('INFERRED', 'UNKNOWN', 'CONFLICTED')),
    source_ref TEXT NOT NULL DEFAULT '',
    import_id BIGINT NOT NULL REFERENCES library_imports(id),
    UNIQUE (origin, kind, from_id, to_id, relation)
);

CREATE TABLE IF NOT EXISTS library_reviews (
    id BIGSERIAL PRIMARY KEY,
    record_id BIGINT NOT NULL REFERENCES library_records(id),
    status TEXT NOT NULL CHECK (status IN ('reviewed', 'verified', 'rejected', 'staged')),
    note TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    decided_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS library_links (
    id BIGSERIAL PRIMARY KEY,
    record_id BIGINT NOT NULL REFERENCES library_records(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK (target_type IN (
        'project', 'capability', 'category', 'graph-source', 'module', 'standard')),
    target_id BIGINT NOT NULL,
    basis TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    decided_by TEXT,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    UNIQUE (record_id, target_type, target_id)
);

CREATE TABLE IF NOT EXISTS library_tasks (
    id BIGSERIAL PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'design-audit', 'reconcile', 'interface', 'dependency', 'enterprise-evidence',
        'discovery', 'bootstrap-cycle', 'scope', 'licence', 'change-review', 'conflict',
        'missing')),
    subject TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    priority SMALLINT NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'dismissed')),
    note TEXT,
    import_id BIGINT REFERENCES library_imports(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS library_tasks_open ON library_tasks (status, priority);
