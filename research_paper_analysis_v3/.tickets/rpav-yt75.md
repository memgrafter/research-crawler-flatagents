---
id: rpav-yt75
status: open
deps: []
links: []
created: 2026-06-13T06:55:11Z
type: feature
priority: 2
assignee: memgrafter
---
# Resolve frontmatter generation source

The design doc doesn't specify how YAML frontmatter gets generated. Is it from an LLM call (which fan-out mode?), non-agentic extraction from metadata, or prep machine output? Need to decide before implementing the analyzer machine end-to-end. Options: 1) Add a dedicated fan-out mode for frontmatter, 2) Bundle with narrative_lead mode, 3) Extract non-agently from prep metadata + paper header parsing.

## Notes

**2026-06-13T07:01:20Z**

More context: _prepend_frontmatter_v3 currently extracts key_metric and baseline_beaten via fragile regex but is missing: year, domain, task, novelty_level, reproducibility, code_available_at_publication. Options: 1) dedicated fan-out mode, 2) bundle with narrative_lead, 3) non-agentic extraction from prep metadata, 4) hybrid.
