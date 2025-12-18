-- Migration: Add persistent API tokens table with RLS
-- Purpose: Store API tokens securely in PostgreSQL instead of volatile Valkey
-- Tokens are hashed (SHA256) before storage - raw token shown once at creation only

BEGIN;

-- API tokens table - stores hashed tokens with user binding
CREATE TABLE IF NOT EXISTS api_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,  -- SHA256 hex = 64 chars
    name VARCHAR(100) NOT NULL DEFAULT 'API Token',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,  -- NULL = never expires
    last_used_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE  -- soft delete for audit trail
);

-- Index for fast token validation (most common operation)
CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash) WHERE revoked_at IS NULL;

-- Index for listing user's tokens
CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id) WHERE revoked_at IS NULL;

-- Enable RLS for user isolation
ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY;

-- Users can only see/manage their own tokens
CREATE POLICY api_tokens_user_policy ON api_tokens
    FOR ALL TO PUBLIC
    USING (user_id = current_setting('app.current_user_id', true)::uuid);

-- Grant permissions to application role
GRANT SELECT, INSERT, UPDATE, DELETE ON api_tokens TO mira_dbuser;

COMMIT;
