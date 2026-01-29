-- Worker Registry Table
-- Extends the arxiv_crawler schema with worker lifecycle management
-- Run this migration to add distributed worker support

CREATE TABLE IF NOT EXISTS worker_registry (
    worker_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active',
    host TEXT,
    pid INTEGER,
    capabilities TEXT,  -- JSON array
    pool_id TEXT,
    started_at TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL,
    current_task_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_worker_registry_status ON worker_registry(status);
CREATE INDEX IF NOT EXISTS idx_worker_registry_heartbeat ON worker_registry(last_heartbeat);

-- Add claimed_by column to paper_queue if not exists
-- This tracks which worker is processing each paper
ALTER TABLE paper_queue ADD COLUMN claimed_by TEXT;
ALTER TABLE paper_queue ADD COLUMN claimed_at TEXT;
ALTER TABLE paper_queue ADD COLUMN attempts INTEGER DEFAULT 0;
ALTER TABLE paper_queue ADD COLUMN max_retries INTEGER DEFAULT 3;
