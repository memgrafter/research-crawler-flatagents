-- Scaling Events Queue Migration
-- Run this to add scaling event triggers for worker replenishment

CREATE TABLE IF NOT EXISTS scaling_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,  -- 'worker_done', 'worker_failed', 'manual'
    worker_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_scaling_events_id ON scaling_events(id);
