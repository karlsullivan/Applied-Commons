-- Three more enablers (maintainer decision 2026-10-07; taxonomy.md,
-- Enablers).

INSERT INTO need_categories (layer, name, scope, track) VALUES
    (NULL, 'Fabrication and tools', 'Open machines, tools and processes that let people make and repair what essential needs depend on: workshop machines, low-cost manufacturing, repair and spare parts.', 'enabler'),
    (NULL, 'Transport and logistics', 'Moving people, goods, water and supplies that essential needs depend on: low-cost vehicles and carriers, last-mile delivery, supply-chain tools.', 'enabler'),
    (NULL, 'Sensing and monitoring', 'Measuring what essential needs depend on: low-cost sensors and test equipment for water quality, air, soil, health and infrastructure, with open data and calibration.', 'enabler')
ON CONFLICT (name) DO NOTHING;
