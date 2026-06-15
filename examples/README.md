# Example projects

These are sample project files that show the legwork project file format. They
are not real projects and nothing here runs against a real repo. They exist so
you can see the frontmatter keys, the optional `## Vision` section, the
`## Next prompt` block and the append-only `## Log` before you write your own.

The three samples cover a range of states:

- `examples/projects/garden-planner.md`: queued and autonomous (`autonomy: loop`),
  with a full Vision section and a cold-start next prompt.
- `examples/projects/recipe-box.md`: a manual project in review, no autonomy.
- `examples/projects/budget-cli.md`: queued but held back by a `blocked_on`
  dependency (the runner refuses to fire while `blocked_on` is set), with a
  reopen-style next prompt.

You would keep your own projects in a private legwork repo (`$LEGWORK_DIR`,
default `$HOME/legwork`), not here. The top-level `/projects/` directory in
this repo is gitignored for exactly that reason. Copy a sample, change the
frontmatter, and point `repo:` at your own code.
