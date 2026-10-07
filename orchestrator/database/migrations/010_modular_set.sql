-- Stage 2, the modular set (policy section 4e; maintainer decisions
-- 2026-10-07): interface standards (approved by the maintainer), modules
-- built on them, systems composed of modules for five scenarios, and a
-- monthly roadmap (approved by the maintainer). Plus private site
-- profiles (the proving ground), which never enter the repository.

CREATE TABLE IF NOT EXISTS standards (
    id BIGSERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    spec TEXT NOT NULL,
    rationale TEXT NOT NULL,
    used_by JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected')),
    decision_note TEXT,
    decided_at TIMESTAMPTZ,
    job_id BIGINT REFERENCES jobs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS modules (
    id BIGSERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    domain TEXT NOT NULL,
    maturity TEXT NOT NULL DEFAULT 'concept'
        CHECK (maturity IN ('concept', 'documented', 'built', 'tested')),
    cost_eu DOUBLE PRECISION,
    cost_low_income DOUBLE PRECISION,
    standards JSONB NOT NULL DEFAULT '[]'::jsonb,
    projects JSONB NOT NULL DEFAULT '[]'::jsonb,
    spec JSONB NOT NULL,
    job_id BIGINT REFERENCES jobs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS systems (
    id BIGSERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    scenario TEXT NOT NULL,
    cost_eu DOUBLE PRECISION,
    cost_low_income DOUBLE PRECISION,
    modules JSONB NOT NULL DEFAULT '[]'::jsonb,
    spec JSONB NOT NULL,
    job_id BIGINT REFERENCES jobs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS roadmaps (
    id BIGSERIAL PRIMARY KEY,
    version TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    summary TEXT NOT NULL,
    steps JSONB NOT NULL,
    decision_note TEXT,
    decided_at TIMESTAMPTZ,
    job_id BIGINT REFERENCES jobs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Proving-ground profiles, set from a private file on the host
-- (POST /sites); used by roadmap revisions, never committed.
CREATE TABLE IF NOT EXISTS sites (
    code TEXT PRIMARY KEY,
    profile JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
