# Example projects

These are sample project files that show the legwork project file format. They
are not real projects and nothing here runs against a real repo. They exist so
you can see the frontmatter keys, the optional `## Vision` section, the
`## Next prompt` block and the append-only `## Log` before you write your own.

There is one sample per status, so the set covers the full spread the
dashboard renders (`escalated`, `queued`, `running`, `review`, `done`,
`icebox`):

- `examples/projects/link-checker.md`: `escalated` and autonomous
  (`autonomy: loop`). An autonomous session hit a guardrail and stopped with a
  DECISION NEEDED brief as its next prompt, so it lands in the dashboard's
  needs-you zone. It also carries a `blocked_on` line, which the runner treats
  as a hard stop: it never fires a project while `blocked_on` is set.
- `examples/projects/garden-planner.md`: `queued` and autonomous
  (`autonomy: loop`), with a full Vision section and a cold-start next prompt.
- `examples/projects/habit-tracker.md`: `running` and autonomous
  (`autonomy: loop`). This is the state a project sits in while a headless
  session is mid-flight; the next prompt is the one being executed.
- `examples/projects/recipe-box.md`: `review`, a manual project waiting on a
  human look before it ships. No autonomy.
- `examples/projects/budget-cli.md`: `done`. A finished project, kept for the
  record with a reopen note as its next prompt.
- `examples/projects/photo-renamer.md`: `icebox`, shelved with the reason in
  the log and a reopen-style next prompt.

You would keep your own projects in a private legwork repo (`$LEGWORK_DIR`,
default `$HOME/legwork`), not here. The top-level `/projects/` directory in
this repo is gitignored for exactly that reason. Copy a sample, change the
frontmatter, and point `repo:` at your own code.
