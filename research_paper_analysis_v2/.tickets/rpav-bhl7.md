---
id: rpav-bhl7
status: open
deps: []
links: [rpav-uj2i]
created: 2026-06-13T03:02:03Z
type: task
priority: 2
assignee: memgrafter
tags: [research, prep]
---
# Add clean_paper prep action

New hook action between extract_text and write_key_outcome. Strips [PAGE N] markers, collapses excessive blank lines, joins broken line-wraps (hyphen + lowercase), removes reference section, removes appendix/supplementary sections, normalizes encoding artifacts. Writes cleaned_paper_text to context.
