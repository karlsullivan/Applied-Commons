-- Non-commercial designs (maintainer decision 2026-10-23, policy sections
-- 4c and 9). AC is non-commercial, so a design under a non-commercial
-- licence (CC BY-NC, BY-NC-SA, TAPR NCL) may be archived and published,
-- marked non-commercial, and built on sparingly when no open design
-- serves; the derivative keeps that licence and is labelled non-commercial.
-- Such licences were classed 'restricted' with no-derivatives ones; they
-- get their own class. Idempotent: deploys re-run every migration.
ALTER TABLE source_checks DROP CONSTRAINT IF EXISTS source_checks_licence_class_check;
ALTER TABLE source_checks ADD CONSTRAINT source_checks_licence_class_check
    CHECK (licence_class IN ('open', 'share-alike', 'non-commercial', 'restricted', 'none',
                             'unknown'));
ALTER TABLE implementations DROP CONSTRAINT IF EXISTS implementations_licence_class_check;
ALTER TABLE implementations ADD CONSTRAINT implementations_licence_class_check
    CHECK (licence_class IN ('open', 'share-alike', 'non-commercial', 'restricted', 'none',
                             'unknown'));

-- Earlier checks, classed as ops/checks/check_sources.py now classes them.
UPDATE source_checks SET licence_class = 'non-commercial'
WHERE licence_class = 'restricted'
  AND licence ~* '(\mNC\M|non-?commercial)'
  AND licence ~* '(\mCC\M|creative commons|attribution|\mTAPR\M)'
  AND licence !~* '(\mND\M|no-?deriv|polyform|commons clause|\mSSPL\M|\mBUSL\M|business source|elastic licen)';
