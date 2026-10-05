-- Autonomous research pipeline: need categories (from policies/taxonomy.md),
-- candidate projects, rubric assessments, and recorded decisions.

CREATE TABLE IF NOT EXISTS need_categories (
    id BIGSERIAL PRIMARY KEY,
    layer SMALLINT NOT NULL CHECK (layer BETWEEN 1 AND 5),
    name TEXT UNIQUE NOT NULL,
    scope TEXT NOT NULL
);

INSERT INTO need_categories (layer, name, scope) VALUES
    (1, 'Air and breathing', 'Breathable air, ventilation and protection from harmful airborne exposure.'),
    (1, 'Water', 'Sufficient, suitable water; collection, treatment, storage and delivery.'),
    (1, 'Food and nutrition', 'Food availability, nutritional adequacy, production, preparation and preservation.'),
    (1, 'Sanitation and hygiene', 'Toileting, washing, cleanliness and safe handling of human waste.'),
    (1, 'Shelter and thermal comfort', 'Habitable spaces, weather protection, temperature and humidity control.'),
    (1, 'Clothing and bodily protection', 'Clothing, footwear and protection from environmental exposure.'),
    (1, 'Sleep and rest', 'Conditions supporting adequate sleep, physical rest and recovery.'),
    (1, 'Reproductive and early-life needs', 'Reproductive needs and the basic requirements of infants; clinical care also connects to health.'),
    (2, 'Physical and mental health', 'Prevention, access to care, treatment, rehabilitation and ongoing support, including maternal and infant healthcare.'),
    (2, 'Personal safety and protection', 'Protection from injury, violence and hazardous environments; humanitarian hazard detection.'),
    (2, 'Mobility and independent living', 'Moving around, self-care and carrying out essential daily activities.'),
    (2, 'Continuity of essential services', 'Dependable services and recovery from failures, disasters and supply disruptions.'),
    (2, 'Economic and livelihood security', 'Reliable access to necessities, sustainable livelihoods and manageable recurring costs.'),
    (2, 'Environmental stability and resource security', 'Pollution reduction, healthy ecosystems, durable carbon storage and essential resource availability.'),
    (3, 'Communication and relationships', 'Maintaining contact, exchanging information, friendship, family relationships and intimacy.'),
    (3, 'Community and participation', 'Mutual support, inclusion, cooperation and participation in community life.'),
    (4, 'Learning, competence and achievement', 'Education, practical skills, confidence, accomplishment and recognition.'),
    (4, 'Autonomy and productive capability', 'Making meaningful choices, contributing through useful work and exercising control over daily life.'),
    (5, 'Creativity and discovery', 'Creative expression, scientific enquiry, exploration and developing personal potential.'),
    (5, 'Meaning and purposeful contribution', 'Personal growth, purposeful activity and contributing to something people value.')
ON CONFLICT (name) DO NOTHING;

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS category_id BIGINT REFERENCES need_categories(id),
    ADD COLUMN IF NOT EXISTS summary TEXT,
    ADD COLUMN IF NOT EXISTS source_uris JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS discovered_by_job BIGINT REFERENCES jobs(id);

-- One candidate per name within a category (case-insensitive).
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_category_name
    ON projects (category_id, lower(name))
    WHERE category_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS assessments (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id),
    job_id BIGINT REFERENCES jobs(id),
    rubric_version TEXT NOT NULL,
    scores JSONB NOT NULL,
    composite DOUBLE PRECISION NOT NULL,
    requirements JSONB NOT NULL,
    rationale TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS decisions (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES projects(id),
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    author TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
