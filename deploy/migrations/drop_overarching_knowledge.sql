-- Migration: Drop overarching_knowledge column from users table
-- Created: 2025-12-11
-- Reason: Refactored to use first_name directly in system prompt substitution
-- Run with: psql -U mira_admin -h localhost -d mira_service -f deploy/migrations/drop_overarching_knowledge.sql

BEGIN;

-- Drop overarching_knowledge column from users table
ALTER TABLE users
DROP COLUMN IF EXISTS overarching_knowledge;

-- Drop overarching_knowledge column from users_trash table for consistency
ALTER TABLE users_trash
DROP COLUMN IF EXISTS overarching_knowledge;

COMMIT;

-- Verify changes
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'users'
AND column_name = 'overarching_knowledge';
