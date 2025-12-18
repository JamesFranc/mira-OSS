-- Migration: Rename tier identifiers to semantic names
-- Converts: default → balanced, deep → nuanced
-- Run manually on existing databases (003 handles fresh installs)

BEGIN;

-- Step 1: Drop FK constraint temporarily
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_llm_tier_fkey;

-- Step 2: Update user preferences to new tier names
UPDATE users SET llm_tier = 'balanced' WHERE llm_tier = 'default';
UPDATE users SET llm_tier = 'nuanced' WHERE llm_tier = 'deep';

-- Step 3: Rename tiers in account_tiers (PK updates)
UPDATE account_tiers SET name = 'balanced', description = 'Sonnet with light reasoning' WHERE name = 'default';
UPDATE account_tiers SET name = 'nuanced', description = 'Opus with nuanced reasoning' WHERE name = 'deep';

-- Step 4: Update the default column value
ALTER TABLE users ALTER COLUMN llm_tier SET DEFAULT 'balanced';

-- Step 5: Re-add FK constraint
ALTER TABLE users ADD CONSTRAINT users_llm_tier_fkey
    FOREIGN KEY (llm_tier) REFERENCES account_tiers(name);

COMMIT;

-- Verification
SELECT 'account_tiers after migration:' as info;
SELECT * FROM account_tiers ORDER BY display_order;

SELECT 'users.llm_tier distribution:' as info;
SELECT llm_tier, COUNT(*) as user_count FROM users GROUP BY llm_tier;
