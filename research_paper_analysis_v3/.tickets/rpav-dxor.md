---
id: rpav-dxor
status: closed
deps: [rpav-b3m1]
links: [rpav-8sq6, rpav-qqed, rpav-e50g]
created: 2026-06-13T06:47:53Z
type: feature
priority: 2
assignee: memgrafter
---
# Write prep machine (download, extract) — CORE DONE

v3 needs its own prep machine: download_pdf → extract_docling. No LLM calls.

## Done
- `config/prep_machine.yml`: prep pipeline YAML config
- `_download_pdf` action: 3-tier strategy (GCS/gsutil → export.arxiv.org → arxiv.org)
- `_extract_docling` action: lightweight Docling pipeline (no OCR, force_backend_text)
- e2e integration test: real PDF download from Kaggle/GCS + Docling extraction for 1706.03762

## Remaining (split into separate tickets)
- clean_paper: rpav-qqed
- save_prep_result: rpav-e50g
- collect_corpus_signals: rpav-eew6 (cancelled)
