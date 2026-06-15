---
name: Budget CLI
category: personal
status: queued
energy: light
description: A terminal tool that summarises a CSV of transactions by category.
repo: ~/code/budget-cli
updated: 2026-04-18
blocked_on: waiting on the bank to publish a stable CSV export format
---

## Next prompt

```text
Reopen when the bank export settles: re-read the sample CSV in examples/, then
update the column mapping in parse.py to match the new headers before doing
anything else. Pick up from the category-rollup feature that was half done.
```

## Log

- 2026-04-18: Parked. The bank changed its CSV columns twice this month, so the parser keeps breaking; blocked until the export format is stable.
- 2026-04-11: Category rollup half built; totals per category print but the month filter is not wired yet.
- 2026-04-04: First pass reads a CSV and prints a grand total. Column mapping is hardcoded to the current export.
