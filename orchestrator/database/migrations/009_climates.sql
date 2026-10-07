-- Climate groups a project works in year-round (policy section 2b;
-- maintainer decision 2026-10-07), from its latest assessment or review.
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS climates JSONB NOT NULL DEFAULT '[]'::jsonb;
