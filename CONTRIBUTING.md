# Contributing

Thanks for looking. Read this first so you know what to expect.

## Stance

legwork is shipped as-is. It is an opinionated, Claude-Code-first proof of
capability, not a product. It works for one person's setup and is published in
case the pattern is useful to you.

Issues are welcome: bug reports, sharp edges, and questions all help.

Pull requests are optional. Small, focused ones are easier to take. There is no
SLA, no guarantee of a reply, and no guarantee of a merge. If you want a change
and it does not land here, fork it.

## Out of scope

These are deliberate non-goals. PRs that head this way will be declined.

- No multi-vendor or multi-model breadth. This is Claude-Code-first on purpose.
- No hosted service, agent-ops platform, or paid tier. No scope creep toward one.
- No rewrites and no gold-plating. Keep it small.

## House rules for any change

- Keep the test suite green: `python3 -m unittest discover -s tests` must pass.
- `core/build_dashboard.py` and the tests stay stdlib-only. Add no new
  dependencies.
- Plain, direct prose in code, comments, and docs. No em dashes anywhere.
- `core/` is the single source for the level-1 loop. The Claude Code plugin
  is `core/` itself, and `scripts/build_wedge.py` republishes `core/` as the
  standalone wedge repo — both are generated, never hand-copied. Change a verb
  or the dashboard once, in `core/`. If you edit `core/`, rerun
  `python3 scripts/build_wedge.py` so `dist/wedge/` (a gitignored build
  artifact) tracks it; `--check` and `tests/test_wedge.py` fail on drift.

## Running the tests

From the repo root:

```
python3 -m unittest discover -s tests
```

Add `-v` for per-test output:

```
python3 -m unittest discover -s tests -v
```
