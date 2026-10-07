-- Enablers: a track alongside the Maslow layers (maintainer decision
-- 2026-10-07; policy section 2a). Enabler categories have no layer; an
-- enabler project gets the most basic layer among the needs it serves
-- once it is assessed.

ALTER TABLE need_categories
    ADD COLUMN IF NOT EXISTS track TEXT NOT NULL DEFAULT 'need'
        CHECK (track IN ('need', 'enabler'));
ALTER TABLE need_categories ALTER COLUMN layer DROP NOT NULL;
ALTER TABLE projects ALTER COLUMN maslow_level DROP NOT NULL;

-- The need categories an enabler project serves (from its assessment).
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS serves JSONB NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO need_categories (layer, name, scope, track) VALUES
    (NULL, 'Energy', 'Generation, storage and distribution of energy that essential needs depend on: off-grid and small-grid power, efficient end use, cooking and pumping energy.', 'enabler'),
    (NULL, 'Communications and information', 'Connectivity and information systems that essential needs depend on: low-cost networks, early warning, access to health, market and technical information.', 'enabler'),
    (NULL, 'Environment and climate', 'Environmental systems that all needs depend on: carbon removal and durable storage, ecosystem restoration, climate adaptation. Exposure to pollution and resource access stay under the Safety layer.', 'enabler')
ON CONFLICT (name) DO NOTHING;
