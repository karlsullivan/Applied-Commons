-- More enablers (maintainer decisions 2026-10-07; taxonomy.md, Enablers).

INSERT INTO need_categories (layer, name, scope, track) VALUES
    (NULL, 'Fabrication and tools', 'Open machines, tools and processes that let people make and repair what essential needs depend on: workshop machines, low-cost manufacturing, repair and spare parts.', 'enabler'),
    (NULL, 'Transport and logistics', 'Moving people, goods, water and supplies that essential needs depend on: low-cost vehicles and carriers, last-mile delivery, supply-chain tools.', 'enabler'),
    (NULL, 'Instrumentation and control', 'Measuring and controlling what essential needs depend on: low-cost sensors and test equipment for water quality, air, soil, health and infrastructure, with open data and calibration; controllers and automation for pumps, irrigation, microgrids and treatment systems (open PLCs, actuators).', 'enabler'),
    (NULL, 'Skills and know-how', 'Practical skills that essential needs depend on: open training and build guides, maintenance and repair knowledge, local technician programmes.', 'enabler'),
    (NULL, 'Computing and open data', 'Computing and data that essential needs depend on: low-power and offline-first computing, open software platforms, open datasets and data infrastructure.', 'enabler'),
    (NULL, 'Cold chain and storage', 'Keeping what essential needs depend on cool, dry and safe in storage: off-grid refrigeration for food and medicines, evaporative and thermal cooling, airtight grain storage. Moving goods stays under Transport and logistics.', 'enabler'),
    (NULL, 'Mapping and geospatial', 'Location data that essential needs depend on: open mapping, site and resource surveys, humanitarian and disaster mapping.', 'enabler'),
    (NULL, 'Finance and payments', 'Ways to pay for, finance and sustain what essential needs depend on: mobile money, savings groups, pay-as-you-go models, cooperative accounting tools.', 'enabler'),
    (NULL, 'Materials and circularity', 'Materials that essential needs depend on: local and low-cost building and manufacturing materials, recycling and reuse, low-embodied-carbon inputs.', 'enabler')
ON CONFLICT (name) DO NOTHING;
