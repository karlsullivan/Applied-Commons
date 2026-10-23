-- Maintainer priority (policy section 4d; maintainer decision 2026-10-23).
-- A priority project is admitted as soon as it is assessed, outside the
-- slot limit and the admission threshold, and its jobs are claimed ahead
-- of everything else. It still goes through research and the go/no-go
-- review. prioritised_at stays set when the priority is cleared, so a
-- focused discovery picks its best candidate only once.
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS priority BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS priority_note TEXT,
    ADD COLUMN IF NOT EXISTS prioritised_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_projects_priority ON projects (id) WHERE priority;
