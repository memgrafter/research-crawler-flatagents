---
id: rpav-je6b
status: closed
deps: []
links: [rpav-8sq6]
created: 2026-06-21T21:30:56Z
type: feature
priority: 1
assignee: trent
tags: [infrastructure, storage]
---
# Add S3-compatible object store backend for FlatMachines

Implement S3-compatible (MinIO compatible) object store backends for FlatMachines persistence and result storage.

Currently the codebase uses:
- SQLiteCheckpointBackend for persistence (local disk, single-process)
- InMemoryResultBackend for inter-machine results
- No distributed checkpoint or result storage

Create new modules:
- S3CheckpointBackend — implements PersistenceBackend using aioboto3/s3fs for async S3 operations
- S3ResultBackend — implements ResultBackend using the same S3 client

Key requirements:
- Support both AWS S3 and MinIO via configurable endpoint URL (s3.endpoint_url)
- URI scheme: s3://bucket/prefix/execution_id/... for checkpoints, s3://bucket/results/{execution_id}/{path} for results
- Configurable bucket name, prefix, region, credentials
- Async operations only (aioboto3)
- Follow FirestoreBackend pattern as reference — it already has both Persistence and Result backend implementations

This unblocks distributed checkpoint persistence across machines and shared result storage.
