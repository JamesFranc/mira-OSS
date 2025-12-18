-- Migration: IVFFlat -> HNSW for segment embedding index
--
-- IVFFlat with default probes=1 returns incomplete results for sparse data.
-- HNSW adapts automatically and requires no tuning.
--
-- Safe to run online - DROP/CREATE INDEX is fast for small tables.

BEGIN;

-- Drop existing IVFFlat index
DROP INDEX IF EXISTS idx_messages_segment_embedding;

-- Create HNSW index (no lists/probes tuning required)
CREATE INDEX idx_messages_segment_embedding ON messages
    USING hnsw (segment_embedding vector_cosine_ops)
    WHERE metadata->>'is_segment_boundary' = 'true'
      AND segment_embedding IS NOT NULL;

COMMIT;
