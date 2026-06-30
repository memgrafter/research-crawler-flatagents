---
id: rpav-tpcr
status: open
deps: []
links: [rpav-8sq6]
created: 2026-06-21T21:31:00Z
type: feature
priority: 2
assignee: trent
tags: [embeddings, local-gpu]
---
# Add embeddings via local GPU (vert/localhost) running llama.cpp

Add embedding generation capability using the existing local GPU infrastructure.

Currently no embeddings exist in the pipeline. Options:
1. Local GPU route (preferred): Extend the existing llama.cpp serving at 192.168.1.21 to serve embeddings — consistent with existing KV cache warmup infrastructure, no cost, no rate limits
2. Free/cheap API: Google text-embedding-004 (200K tokens/day free tier)

Local GPU approach:
- Add embedding model config to profiles.yml (new 'embeddings' profile pointing to local GPU)
- Create embedding generation utility (similar to KV cache warmup but for text embeddings)
- Store embeddings as .npy files keyed by arxiv_id + model name
- Use a lightweight vector index (hnswlib) for similarity search across digests

Use cases:
- Corpus-level deduplication before analysis
- Semantic search across digests
- Grouping similar papers for synthesis

Considerations:
- Need to confirm which GPU model (3090Ti) and what embedding model to run
- llama.cpp embedding endpoint setup — need to verify existing serving supports this
