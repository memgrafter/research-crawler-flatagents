---
id: rpav-cczj
status: open
deps: []
links: [rpav-uj2i]
created: 2026-06-13T03:02:08Z
type: task
priority: 2
assignee: memgrafter
tags: [research, prep]
---
# Add ar5iv HTML download with PDF fallback

Fetch paper from ar5iv.labs.arxiv.org/html/<id> as primary source (cleaner math, no line-wrap artifacts). Fall back to PDF + pypdf extraction if ar5iv returns non-200 or times out. Add new hook action get_ar5iv_html.
