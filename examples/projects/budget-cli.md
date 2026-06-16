---
name: Budget CLI
category: personal
status: done
energy: light
description: A terminal tool that summarises a CSV of transactions by category.
repo: ~/code/budget-cli
updated: 2026-06-09
---

## Next prompt

```text
Done, nothing queued. If the bank changes its CSV export again, reopen and
update the column mapping in parse.py before anything else.
```

## Log

- 2026-06-09: Done. Category rollup and the month filter both ship: `budget summarise statement.csv --month 2026-05` prints per-category totals for the month and a grand total. Tests cover the parser and the date filter.
- 2026-06-02: Bank export format finally settled, so the column mapping is stable now. Wired the month filter to the parser.
- 2026-04-11: Category rollup half built; totals per category print but the month filter is not wired yet.
