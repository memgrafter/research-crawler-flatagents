---
id: rpav-v7d7
status: open
deps: [rpav-bhl7, rpav-cczj]
links: [rpav-uj2i]
created: 2026-06-13T03:06:50Z
type: task
priority: 2
assignee: memgrafter
tags: [research, prep]
---
# Update prep_machine.yml with clean_paper and ar5iv integration

Wire clean_paper action and ar5iv download into prep_machine.yml. Change write_key_outcome input from paper_text to cleaned_paper_text. Ensure key_outcome is produced before unified_machine runs so it's available in prep_output for the analyzer.
