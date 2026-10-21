-- Civilisation Capability Graph, phase 1: the data model (policy section
-- 4k and capability-graph.md; maintainer decision 2026-10-21).
--
-- It sits below the existing layer and category model and does not change
-- it. Need records give a category's needs with their minimum, baseline and
-- improved thresholds. Capabilities are what a community must be able to do
-- to meet them; each serves one or more categories or needs and may require
-- other capabilities. Implementations are ways of providing a capability
-- (purchase, salvage, recondition, fabricate, bootstrap, experimental),
-- each with a bootstrap level (what it takes to make) and an evidence
-- maturity (how well it is shown to work): two axes separate from the
-- Maslow layer. Sources carry provenance and licence. Assessments are
-- claims about a record, each with an evidence state and its source, kept
-- side by side when they disagree.
--
-- Needs, capabilities and implementations enter as candidates and become
-- authoritative only when approved. Every write takes the next graph
-- revision, so a published page can say which revision it came from.

CREATE SEQUENCE IF NOT EXISTS graph_revision;

-- One row per candidate graph update (an extraction job, an assistant or
-- the maintainer), for provenance.
CREATE TABLE IF NOT EXISTS graph_updates (
    id BIGSERIAL PRIMARY KEY,
    origin TEXT NOT NULL CHECK (origin IN ('applied-commons', 'maintainer', 'external-human')),
    proposed_by TEXT NOT NULL,
    job_id BIGINT REFERENCES jobs(id),
    agent_run TEXT,
    summary TEXT NOT NULL DEFAULT '',
    counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- An unknown licence is unverified and not redistributable (section 34).
-- Applied Commons' own publications are sources of origin applied-commons
-- and never count as independent evidence (section 32).
CREATE TABLE IF NOT EXISTS graph_sources (
    id BIGSERIAL PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    family TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN (
        'mediawiki', 'git', 'book_manual', 'technical_collection',
        'established_engineering', 'standard', 'web', 'other')),
    title TEXT NOT NULL,
    uri TEXT,
    revision TEXT,
    bibliographic JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_quality TEXT NOT NULL DEFAULT 'unknown'
        CHECK (source_quality IN ('low', 'medium', 'high', 'unknown')),
    source_authority TEXT NOT NULL DEFAULT 'unknown' CHECK (source_authority IN (
        'standards-body', 'peer-reviewed', 'established-reference', 'manufacturer',
        'practitioner', 'community', 'applied-commons', 'unknown')),
    origin TEXT NOT NULL DEFAULT 'external' CHECK (origin IN ('external', 'applied-commons')),
    license_original TEXT NOT NULL DEFAULT 'unknown',
    license_verified BOOLEAN NOT NULL DEFAULT false,
    attribution TEXT NOT NULL DEFAULT '',
    derivative_status TEXT NOT NULL DEFAULT 'none'
        CHECK (derivative_status IN ('none', 'excerpt', 'summary', 'derivative', 'copy')),
    redistribution_allowed BOOLEAN NOT NULL DEFAULT false,
    update_id BIGINT REFERENCES graph_updates(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (license_verified OR NOT redistribution_allowed)
);

CREATE TABLE IF NOT EXISTS needs (
    id BIGSERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    category_id BIGINT NOT NULL REFERENCES need_categories(id),
    name TEXT NOT NULL,
    desired_state TEXT NOT NULL DEFAULT '',
    minimum_threshold TEXT NOT NULL DEFAULT '',
    baseline_threshold TEXT NOT NULL DEFAULT '',
    improved_threshold TEXT NOT NULL DEFAULT '',
    improvements JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Whether open, buildable means meet the baseline (section 39): set by
    -- an assessment or the maintainer, never assumed.
    baseline_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (baseline_status IN ('unknown', 'unmet', 'partial', 'met')),
    baseline_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'approved', 'rejected')),
    proposed_by TEXT NOT NULL,
    update_id BIGINT REFERENCES graph_updates(id),
    decision_note TEXT,
    decided_by TEXT,
    independent_check TEXT,
    decided_at TIMESTAMPTZ,
    revision BIGINT NOT NULL DEFAULT nextval('graph_revision'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS capabilities (
    id BIGSERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    -- materials, tools, skills, energy, infrastructure, knowledge: lists.
    -- Required capabilities are rows in capability_dependencies.
    requires JSONB NOT NULL DEFAULT '{}'::jsonb,
    produces JSONB NOT NULL DEFAULT '[]'::jsonb,
    bootstrap_min SMALLINT CHECK (bootstrap_min BETWEEN 0 AND 7),
    bootstrap_max SMALLINT CHECK (bootstrap_max BETWEEN 0 AND 7),
    external_dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
    self_reproduction_fraction REAL CHECK (self_reproduction_fraction BETWEEN 0 AND 1),
    resilience JSONB NOT NULL DEFAULT '{}'::jsonb,
    failure_modes JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence TEXT NOT NULL DEFAULT 'unknown'
        CHECK (confidence IN ('low', 'medium', 'high', 'unknown')),
    uncertainties JSONB NOT NULL DEFAULT '[]'::jsonb,
    safety_critical BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'approved', 'rejected')),
    proposed_by TEXT NOT NULL,
    update_id BIGINT REFERENCES graph_updates(id),
    decision_note TEXT,
    decided_by TEXT,
    independent_check TEXT,
    decided_at TIMESTAMPTZ,
    revision BIGINT NOT NULL DEFAULT nextval('graph_revision'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (bootstrap_min IS NULL OR bootstrap_max IS NULL OR bootstrap_min <= bootstrap_max)
);

-- The categories and needs a capability serves (its Maslow links): the
-- same capability serves many needs and is not duplicated (section 3).
-- Links and dependencies are claims with an evidence state; the maintainer
-- can reject a wrong one.
CREATE TABLE IF NOT EXISTS capability_links (
    id BIGSERIAL PRIMARY KEY,
    capability_id BIGINT NOT NULL REFERENCES capabilities(id) ON DELETE CASCADE,
    category_id BIGINT NOT NULL REFERENCES need_categories(id),
    need_id BIGINT REFERENCES needs(id) ON DELETE CASCADE,
    relevance TEXT NOT NULL DEFAULT 'required' CHECK (relevance IN ('required', 'supporting')),
    note TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'INFERRED'
        CHECK (state IN ('SUPPORTED', 'INFERRED', 'UNKNOWN', 'CONFLICTED')),
    source_id BIGINT REFERENCES graph_sources(id),
    locator TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rejected')),
    update_id BIGINT REFERENCES graph_updates(id),
    revision BIGINT NOT NULL DEFAULT nextval('graph_revision'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (state <> 'SUPPORTED' OR source_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS capability_links_unique
    ON capability_links (capability_id, category_id, COALESCE(need_id, 0));

CREATE TABLE IF NOT EXISTS capability_dependencies (
    id BIGSERIAL PRIMARY KEY,
    capability_id BIGINT NOT NULL REFERENCES capabilities(id) ON DELETE CASCADE,
    requires_id BIGINT NOT NULL REFERENCES capabilities(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'requires' CHECK (kind IN ('requires', 'benefits-from')),
    note TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'INFERRED'
        CHECK (state IN ('SUPPORTED', 'INFERRED', 'UNKNOWN', 'CONFLICTED')),
    source_id BIGINT REFERENCES graph_sources(id),
    locator TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rejected')),
    update_id BIGINT REFERENCES graph_updates(id),
    revision BIGINT NOT NULL DEFAULT nextval('graph_revision'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (capability_id, requires_id),
    CHECK (capability_id <> requires_id),
    CHECK (state <> 'SUPPORTED' OR source_id IS NOT NULL)
);

-- The evidence maturity stored here is the one claimed; the API reports
-- the level independent sourced evidence supports, and E0 without it.
CREATE TABLE IF NOT EXISTS implementations (
    id BIGSERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    capability_id BIGINT NOT NULL REFERENCES capabilities(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    pathway TEXT NOT NULL CHECK (pathway IN (
        'purchase', 'salvage', 'recondition', 'fabricate', 'bootstrap',
        'experimental', 'practice', 'other')),
    role TEXT NOT NULL DEFAULT 'candidate_implementation'
        CHECK (role IN ('candidate_implementation', 'baseline', 'alternative')),
    project_id BIGINT REFERENCES projects(id),
    module_id BIGINT REFERENCES modules(id),
    bootstrap_min SMALLINT CHECK (bootstrap_min BETWEEN 0 AND 7),
    bootstrap_max SMALLINT CHECK (bootstrap_max BETWEEN 0 AND 7),
    claimed_maturity_min SMALLINT NOT NULL DEFAULT 0 CHECK (claimed_maturity_min BETWEEN 0 AND 5),
    claimed_maturity_max SMALLINT NOT NULL DEFAULT 0 CHECK (claimed_maturity_max BETWEEN 0 AND 5),
    validation JSONB NOT NULL DEFAULT '{}'::jsonb,
    comparison JSONB NOT NULL DEFAULT '{}'::jsonb,
    requires JSONB NOT NULL DEFAULT '{}'::jsonb,
    external_dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
    failure_modes JSONB NOT NULL DEFAULT '[]'::jsonb,
    licence_class TEXT NOT NULL DEFAULT 'unknown'
        CHECK (licence_class IN ('open', 'share-alike', 'restricted', 'none', 'unknown')),
    safety_critical BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'approved', 'rejected')),
    proposed_by TEXT NOT NULL,
    update_id BIGINT REFERENCES graph_updates(id),
    decision_note TEXT,
    decided_by TEXT,
    independent_check TEXT,
    decided_at TIMESTAMPTZ,
    revision BIGINT NOT NULL DEFAULT nextval('graph_revision'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (bootstrap_min IS NULL OR bootstrap_max IS NULL OR bootstrap_min <= bootstrap_max),
    CHECK (claimed_maturity_min <= claimed_maturity_max)
);
CREATE INDEX IF NOT EXISTS implementations_capability ON implementations (capability_id);

-- Claims about a need, capability or implementation (bootstrap level,
-- evidence maturity, effectiveness, resources, context, a validation
-- answer...). Append-only, so disagreement is kept (section 33). A
-- SUPPORTED claim needs a source; an INFERRED one says its basis.
CREATE TABLE IF NOT EXISTS graph_assessments (
    id BIGSERIAL PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('need', 'capability', 'implementation')),
    subject_id BIGINT NOT NULL,
    attribute TEXT NOT NULL,
    value JSONB NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('SUPPORTED', 'INFERRED', 'UNKNOWN', 'CONFLICTED')),
    source_id BIGINT REFERENCES graph_sources(id),
    locator TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '',
    basis TEXT NOT NULL DEFAULT '',
    assessor TEXT NOT NULL,
    update_id BIGINT REFERENCES graph_updates(id),
    revision BIGINT NOT NULL DEFAULT nextval('graph_revision'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (state <> 'SUPPORTED' OR source_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS graph_assessments_subject
    ON graph_assessments (subject_type, subject_id, attribute);

-- Optional links from catalogue projects into the graph, for project
-- selection (phase 4): the work type (section 28), the capability or
-- implementation it serves, and whether it closes a baseline deficiency or
-- improves an adequate solution (section 27).
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS project_type TEXT CHECK (project_type IN (
        'document', 'open', 'replicate', 'validate', 'improve', 'invent')),
    ADD COLUMN IF NOT EXISTS capability_id BIGINT REFERENCES capabilities(id),
    ADD COLUMN IF NOT EXISTS implementation_id BIGINT REFERENCES implementations(id),
    ADD COLUMN IF NOT EXISTS gap_class TEXT CHECK (gap_class IN ('baseline', 'improvement'));
