-- Public preservation of design sources (policy section 4c; maintainer
-- decision 2026-10-23: designs are captured, not just linked). Beside AC's
-- own pinned copies, every design source of a build pack is held by a
-- public archive: git repositories by Software Heritage (reference: a
-- SWHID), files and pages by the Internet Archive's Wayback Machine
-- (reference: the snapshot URL). Those archives keep material AC's
-- licence rules stop it from republishing. One row per source and
-- service; the newest check replaces the last.
CREATE TABLE IF NOT EXISTS design_preservation (
    id BIGSERIAL PRIMARY KEY,
    source_uri TEXT NOT NULL,
    service TEXT NOT NULL CHECK (service IN ('software-heritage', 'wayback')),
    status TEXT NOT NULL CHECK (status IN ('held', 'requested', 'failed')),
    reference TEXT,
    captured_at TIMESTAMPTZ,
    note TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_uri, service)
);
