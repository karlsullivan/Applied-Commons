-- One project, several needs (policy section 4d; maintainer decision
-- 2026-10-23: duplicate projects are merged). Discovery runs per
-- category, so one project could be found again under another need and
-- catalogued twice. A merged duplicate keeps its records, with status
-- 'merged' and merged_into naming the project that replaces it; the
-- survivor is also listed under the duplicate's category. Discovery now
-- lists a project it finds again under the new category instead of
-- adding it twice.
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS merged_into BIGINT REFERENCES projects(id),
    ADD COLUMN IF NOT EXISTS merged_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS project_categories (
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    category_id BIGINT NOT NULL REFERENCES need_categories(id),
    reason TEXT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, category_id)
);
CREATE INDEX IF NOT EXISTS idx_project_categories_category
    ON project_categories (category_id);
