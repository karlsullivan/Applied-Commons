-- Verification of proposed standards against established ones (ISO, IEC,
-- EN/DIN, AS/NZS and others) before the maintainer decides (policy
-- section 4e; maintainer decision 2026-10-07). verified_hash is the hash
-- of the spec that was verified; a changed spec needs a new verification.
ALTER TABLE standards
    ADD COLUMN IF NOT EXISTS verification JSONB,
    ADD COLUMN IF NOT EXISTS verified_hash TEXT,
    ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
