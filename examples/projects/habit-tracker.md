---
name: Habit Tracker
category: personal
status: running
energy: light
description: A CLI that logs daily habits to a local file and shows current streaks.
repo: ~/code/habit-tracker
updated: 2026-06-16
autonomy: loop
---

## Vision

- North star: `habit done reading` records today against a habit and
  `habit streak` shows the current and longest run for each one, all in a
  single plain-text file the user can read and edit by hand.
- Done means: stdlib only, the data file is human-readable, and the tests
  cover streak maths across month boundaries and missed days.
- Guardrails: local file only, no sync, no accounts, no background daemon.
  Do not add reminders or notifications.
- Escalate when: a change would alter the on-disk file format so that files
  written by an older version no longer load.
- Taste: `habit streak` fits on one screen. Numbers align. A missed day breaks
  a streak quietly, no scolding.

## Next prompt

```text
Read README.md and the last three log entries before you start.

Task: add `habit log <name>` that prints the full dated history for one habit,
newest first, one date per line, so a user can see exactly which days counted.

Done when: `habit log reading` lists every recorded date for that habit newest
first, an unknown habit name exits non-zero with a one-line message, a new test
covers both paths, and `python3 -m unittest` passes.

Model: sonnet

Final step: run /wrap to update the tracker and mint the next prompt.
```

## Log

- 2026-06-16: Session in flight: building the `habit log` history view.
- 2026-06-15: `habit streak` shows current and longest run per habit; streak maths handles missed days and month boundaries. Added the boundary tests.
- 2026-06-12: First version records `habit done <name>` to a plain-text file and lists known habits. Stdlib only.
