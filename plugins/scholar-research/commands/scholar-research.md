---
name: scholar-research
description: Answer a question grounded in academic literature with machine-verified citations
argument-hint: "[your research question]"
---

# /scholar-research

Invoke the **scholar-research** skill to answer `$ARGUMENTS` grounded in academic
literature.

The skill runs a fixed 6-stage pipeline (question decomposition → multi-source
search → screening → close reading → source-tagged synthesis → citation
machine-verification gate) using only free scholarly APIs via
`${CLAUDE_PLUGIN_ROOT}/skills/scholar-research/scripts/scholar.py`.

Every factual claim ends with a `[P#]` tag (papers) or `[W#]` tag (primary time
series from the `data` layer); every reference passes the double gate — DOI/
title/author/year/retraction machine check plus claim-to-excerpt matching —
before the answer is emitted (`인용 검증: N/N MATCH · 주장-근거 대조 m/m 지지`).
Follow the skill's SKILL.md exactly — do not skip the verification gate, and never
fabricate backtest or computed numbers without a `[W#]` series (SKILL.md §10).

If `$ARGUMENTS` is empty, ask the user for their research question first.
