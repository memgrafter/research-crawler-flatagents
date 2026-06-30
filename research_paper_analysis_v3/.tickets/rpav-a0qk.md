---
id: rpav-a0qk
status: closed
deps: []
links: [rpav-8sq6]
created: 2026-06-30T04:27:34Z
type: bug
priority: 2
assignee: memgrafter
tags: [analyzer, debug, fan-out]
---
# Diagnose empty fan-out sections in analyzer pipeline

Full pipeline e2e (prep → analyzer) shows all 7 fan-out sections returning None: narrative_lead, author_uncertainties, method_results, why_mechanism, reproduction, open_questions, limits_confidence.

Need to determine:
- Which LLM calls failed and why (API key? model availability? prompt too long?)
- Whether the cleaned_paper_text is being passed correctly to sub-machines
- Whether the output_to_context routing is working for parallel fan-out
- Whether the judge receives empty input and goes to failed path
