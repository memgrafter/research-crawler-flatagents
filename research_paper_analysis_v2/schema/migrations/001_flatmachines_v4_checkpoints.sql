-- FlatMachines v4 checkpoint schema migration.
-- Apply through run.py/_migrate_v2_db, which guards ALTER TABLE statements by
-- inspecting PRAGMA table_info before changing existing databases.
--
-- Target schema additions:
--   machine_checkpoints.waiting_channel TEXT
--   idx_mc_waiting_channel partial index
--   machine_configs content-addressed config table

CREATE INDEX IF NOT EXISTS idx_mc_waiting_channel
    ON machine_checkpoints(waiting_channel)
    WHERE waiting_channel IS NOT NULL;

CREATE TABLE IF NOT EXISTS machine_configs (
    config_hash  TEXT PRIMARY KEY,
    machine_name TEXT,
    spec_version TEXT,
    config_raw   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
