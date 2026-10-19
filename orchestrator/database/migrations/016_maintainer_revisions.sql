-- The maintainer revises proposed standards and proposes new ones (policy
-- section 4e; maintainer decision 2026-10-19).
--
-- A verification that recommends revising a proposed standard used to
-- leave it stuck: revisions came only from the monthly review of approved
-- standards. The maintainer can now revise a proposed standard in place
-- (the earlier version and its verification move to history) or propose
-- one, and either is verified again before it can be approved. revised_at
-- marks a standard the maintainer wrote or revised; no synthesis rewrites
-- it.
ALTER TABLE standards
    ADD COLUMN IF NOT EXISTS revised_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revision_note TEXT,
    ADD COLUMN IF NOT EXISTS history JSONB NOT NULL DEFAULT '[]'::jsonb;
