---
id: rpav-dxor
status: open
deps: [rpav-b3m1]
links: []
created: 2026-06-13T06:47:53Z
type: feature
priority: 2
assignee: memgrafter
---
# Write prep machine (download, extract, clean paper)

v3 needs its own prep machine: download_pdf → extract_text → clean_paper → collect_corpus_signals → save_prep_result. No LLM calls. Feeds analyzer_machine with cleaned_paper_text, corpus_signals, metadata. Reuse v2 hooks for download/extract; add clean_paper action.
