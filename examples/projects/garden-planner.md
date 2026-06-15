---
name: Garden Planner
category: personal
status: queued
energy: deep
description: A small CLI that plans a vegetable bed by frost dates and spacing.
repo: ~/code/garden-planner
updated: 2026-05-28
autonomy: loop
---

## Vision

- North star: a single-file Python CLI that takes a hardiness zone and a bed
  size and prints a planting calendar with sow, transplant and harvest weeks.
- Done means: `garden plan --zone 8b --bed 4x8` prints a valid calendar for at
  least twenty common vegetables, the plant data lives in one JSON file, and
  the test suite covers the date maths and the spacing layout.
- Guardrails: stdlib only, no third-party packages. Do not add a web UI or a
  database. Keep it one command with subcommands, not a framework.
- Escalate when: a change would alter the published CLI flags, or the plant
  data would need a source you cannot verify, or the work drifts toward a GUI.
- Taste: plain output a person can read in a terminal. Tables align. Errors are
  one helpful line, never a stack trace. Comments explain the date arithmetic.

## Next prompt

```text
Read README.md and the last three log entries in
legwork/projects/garden-planner.md before you start.

Task: add a `--frost-dates` flag to `garden plan` that overrides the
zone-derived last-frost and first-frost dates with two YYYY-MM-DD values the
user passes, then recomputes the calendar from those instead of the zone table.

Done when: `garden plan --zone 8b --bed 4x8 --frost-dates 2026-04-10 2026-11-01`
shifts every sow and harvest week to match the supplied dates, a new test
covers the override path, and `python3 -m unittest` passes.

Model: sonnet

Final step: run /wrap to update the tracker and mint the next prompt.
```

## Log

- 2026-05-28: Spacing layout now packs rows by mature plant width; added the layout test. Next: let the user override frost dates by hand.
- 2026-05-21: Wired the planting calendar to the zone frost table. Twenty-two vegetables in plants.json.
- 2026-05-14: First skeleton: argparse CLI, `plan` and `list` subcommands, stub calendar.
