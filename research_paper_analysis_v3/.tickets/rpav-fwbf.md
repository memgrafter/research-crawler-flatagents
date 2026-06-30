---
id: rpav-fwbf
status: closed
deps: [rpav-b3m1]
links: [rpav-8sq6, rpav-b3m1]
created: 2026-06-30T00:41:07Z
type: feature
priority: 2
assignee: memgrafter
tags: [schema, generated-columns, migration]
---
# Add generated columns to machine_checkpoints for queryable paper metadata

Add SQLite generated columns to the FlatMachines machine_checkpoints table at startup.

## Context

FlatMachines SDK auto-creates the base tables. We need to add generated columns so paper metadata (fmr_score, docling_json_path, digest_path) can be queried efficiently via SQL indexes instead of parsing JSON blobs in Python.

See epic rpav-8sq6 §15 for full design decision.

## Implementation

In PaperManagerHooks.__init__ (or a separate migration module), run ALTER TABLE statements:

```sql
ALTER TABLE machine_checkpoints
ADD COLUMN fmr_score REAL
GENERATED ALWAYS AS (json_extract(snapshot_json, '$.context.fmr_score')) VIRTUAL;

ALTER TABLE machine_checkpoints
ADD COLUMN docling_json_path TEXT
GENERATED ALWAYS AS (json_extract(snapshot_json, '$.context.docling_json_path')) VIRTUAL;

ALTER TABLE machine_checkpoints
ADD COLUMN digest_path TEXT
GENERATED ALWAYS AS (json_extract(snapshot_json, '$.context.digest_path')) VIRTUAL;
```

**Must use VIRTUAL, not STORED** — SQLite won't allow adding STORED columns to tables with existing rows, and the SDK writes checkpoints during execution before our migration runs.

Then create indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_fmr ON machine_checkpoints(fmr_score);
CREATE INDEX IF NOT EXISTS idx_docling ON machine_checkpoints(docling_json_path);
```

Make idempotent — catch SQLite OperationalError if column/index already exists (startup should be safe to call multiple times).

## Depends on

- rpav-b3m1: Schema design decision (reopened with generated columns approach)

