-- Migration: Add account_tiers table and llm_tier user preference
-- Creates the tier system for LLM model selection

BEGIN;

-- Account tiers table (defines available tiers and their LLM configs)
CREATE TABLE IF NOT EXISTS account_tiers (
    name VARCHAR(20) PRIMARY KEY,
    model VARCHAR(100) NOT NULL,
    thinking_budget INT NOT NULL DEFAULT 0,
    description TEXT,
    display_order INT NOT NULL DEFAULT 0
);

-- Seed initial tiers
INSERT INTO account_tiers (name, model, thinking_budget, description, display_order) VALUES
    ('fast', 'claude-haiku-4-5-20251001', 1024, 'Haiku with quick thinking', 1),
    ('balanced', 'claude-sonnet-4-5-20250929', 1024, 'Sonnet with light reasoning', 2),
    ('nuanced', 'claude-opus-4-5-20251101', 8192, 'Opus with nuanced reasoning', 3)
ON CONFLICT (name) DO NOTHING;

-- Add llm_tier column to users with FK to account_tiers
ALTER TABLE users
ADD COLUMN IF NOT EXISTS llm_tier VARCHAR(20) DEFAULT 'balanced' REFERENCES account_tiers(name);

COMMIT;

-- Grant SELECT permission to mira_dbuser (application database user)
GRANT SELECT ON account_tiers TO mira_dbuser;

-- Verification
SELECT 'account_tiers table:' as info;
SELECT * FROM account_tiers ORDER BY display_order;

SELECT 'users.llm_tier column:' as info;
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'users' AND column_name = 'llm_tier';
