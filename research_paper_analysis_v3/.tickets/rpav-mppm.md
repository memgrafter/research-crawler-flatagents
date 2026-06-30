---
id: rpav-mppm
status: closed
deps: [rpav-je6b, rpav-tpcr]
links: [rpav-8sq6]
created: 2026-06-21T21:31:04Z
type: feature
priority: 3
assignee: trent
tags: [embeddings, storage, depends-rpav-je6b, rpav-tpcr]
---
# Store vector embeddings in object store for reuse across runs

Store vector embeddings in an object store so they can be reused across runs and machines.

Depends on:
- rpav-je6b (S3-compatible object store backend) — for the storage layer
- rpav-tpcr (local GPU embeddings) — for the embedding generation

Implementation:
- Store embeddings as .npy or .bin files in object store keyed by:
  - embeddings/{model_name}/{arxiv_id}.npy
- Maintain a metadata index in SQLite mapping arxiv_id → embedding path + model + dimension
- When generating embeddings, first check if they exist in object store (skip generation if already computed)
- Consider using hnswlib for indexing with the actual vectors stored in object store

This enables:
- Reusing embeddings across runs without regenerating
- Sharing embeddings across machines in distributed setups
- Versioning embeddings by model (e.g., change embedding model and re-index)
