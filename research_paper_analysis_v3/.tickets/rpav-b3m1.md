---
id: rpav-b3m1
status: closed
deps: [rpav-8sq6]
links: [rpav-8sq6, rpav-fwbf]
created: 2026-06-13T06:48:17Z
type: feature
priority: 2
assignee: memgrafter
---
# Write v3 DB schema — generated columns on FlatMachines checkpoints

**Design decision recorded in epic rpav-8sq6 §15.**

FlatMachines SDK auto-creates `machine_checkpoints`, `machine_latest`, `machine_configs`, and `execution_leases` via `_ensure_schema()` methods. No standalone schema file needed.

We add **SQLite generated columns** to `machine_checkpoints` at startup for queryable paper metadata:
- `fmr_score REAL GENERATED ALWAYS AS (json_extract(snapshot_json, '$.context.fmr_score')) STORED`
- `docling_json_path TEXT GENERATED ALWAYS AS (json_extract(snapshot_json, '$.context.docling_json_path')) STORED`
- `digest_path TEXT GENERATED ALWAYS AS (json_extract(snapshot_json, '$.context.digest_path')) STORED`

Indexes on generated columns enable efficient queries (e.g. `fmr_score >= 0.6`) without a separate papers table.

Removed `schema/v3_papers.sql` — it duplicated SDK-created tables. Migration script will live in hooks init code.
