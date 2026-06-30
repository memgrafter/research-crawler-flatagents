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

**Selected model: [jinaai/jina-embeddings-v5-text-nano](https://huggingface.co/jinaai/jina-embeddings-v5-text-nano)** — 1024-dim, served via llama.cpp on 3090Ti
- GGUF source: [cstr/jina-v5-nano-GGUF](https://huggingface.co/cstr/jina-v5-nano-GGUF?show_file_info=jina-v5-nano-q8_0.gguf)
- Quantization: Q8_0 (222MB) — good balance of quality/speed, fits easily in VRAM
- Serve with `llama-server` using OpenAI-compatible `/v1/embeddings` endpoint

Use cases:
- Corpus-level deduplication before analysis
- Semantic search across digests
- Grouping similar papers for synthesis

Considerations:
- Verify llama.cpp `/v1/embeddings` endpoint works alongside existing inference serving on localhost
- Later: add persistent .npy storage + hnswlib index if corpus-level search is needed
