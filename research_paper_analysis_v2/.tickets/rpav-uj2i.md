---
id: rpav-uj2i
status: open
deps: []
links: [rpav-bhl7, rpav-cczj, rpav-xf37, rpav-h362, rpav-xqjp, rpav-v7d7]
created: 2026-06-13T03:01:58Z
type: epic
priority: 1
assignee: memgrafter
tags: [research, v2]
---
# Unified single-context paper analysis pipeline

Replace the current 7+ call multi-machine pipeline with a single-context analysis that produces all sections in one LLM call, adding paper cleaning in prep and ar5iv as primary source. Target: ~60% reduction in input tokens, ~50% reduction in LLM calls per paper.
