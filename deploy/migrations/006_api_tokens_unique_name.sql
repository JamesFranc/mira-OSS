-- Migration: Add unique constraint on api_tokens (user_id, name)
-- Purpose: Prevent duplicate token names per user

BEGIN;

-- Add unique constraint (only for non-revoked tokens)
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_tokens_user_name_unique
    ON api_tokens(user_id, name)
    WHERE revoked_at IS NULL;

COMMIT;
