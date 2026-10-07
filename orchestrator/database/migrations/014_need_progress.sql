-- Progress towards "done" per need category (policy section 4h;
-- maintainer decision 2026-10-07: a need is done when modules tested in
-- the real world substantially solve it). A need-progress job matches each
-- brief requirement to what meets it; the API caps each level by facts
-- (build pack complete and licence open or share-alike for documented; a
-- module for designed; a tested module for field-tested). Only the
-- maintainer marks a need substantially solved.
CREATE TABLE IF NOT EXISTS need_progress (
    id BIGSERIAL PRIMARY KEY,
    category_id BIGINT NOT NULL REFERENCES need_categories(id) ON DELETE CASCADE,
    requirements JSONB NOT NULL,
    summary TEXT,
    job_id BIGINT REFERENCES jobs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS need_progress_latest ON need_progress (category_id, created_at DESC);

ALTER TABLE need_categories
    ADD COLUMN IF NOT EXISTS solved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS solved_note TEXT;
