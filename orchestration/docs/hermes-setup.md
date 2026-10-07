# Hermes as the switchboard

The switchboard can be a Hermes Agent thread. It watches every
orchestrator, puts decisions in front of you, relays your answers and
cycles context. This is the setup it needs. Skip it if you relay by hand
(`LEGWORK_SWITCHBOARD=manual`).

## Profiles

A separate profile for the switchboard keeps its memory, skills and model
settings apart from anything else you use Hermes for. Clone one from your
default profile, then pass its name to the installer:

```
python3 orchestration/install.py --with hermes --hermes-profile <profile>
```

`default` installs into `~/.hermes` itself; any other name into
`~/.hermes/profiles/<name>`. The installer writes the switchboard skill to
`skills/orchestration/switchboard/SKILL.md` in the profile, with your
legwork path filled in, and puts your house rules in the profile's
`SOUL.md` between two marker lines (re-running replaces that block).

## Models

- Main thread: a strong model at low reasoning effort is plenty; most of
  its work is reading files and relaying. A cheaper model works once the
  delegations live in the repo files as `GRANT` entries rather than only in
  thread memory.
- Planning sessions: your strongest model. They grill the plan and write
  the roadmap the long run decides from.
- Delegated children: a mid-tier model is enough (for example
  `delegation.model: claude-sonnet-5` with `delegation.provider: anthropic`).
- Reset the switchboard thread daily once the delegations live in the repo
  files as `GRANT` entries; the files hold the state, so nothing is lost.
- Set `prompt_caching.cache_ttl: 1h` in the profile. A thread that wakes
  every 30 minutes on the default 5 minute TTL rewrites its whole context
  each time instead of reusing the cache.

## Skills

Planning sessions in Hermes load `grilling`, `domain-modeling` and
`legwork-tracker`. Import your Claude Code skills into the profile:

```
hermes -p <profile> import-agent claude-code --source <your Claude config dir> --yes
```

`grilling` and `domain-modeling` come from the mattpocock-skills plugin;
install it in Claude Code first if you want them. Then remove the ones that
only work inside Claude Code (`herdr`, `fanout`, `legwork-onboard` and any
other that drives Claude Code itself). Check the imported `MEMORY.md` too:
Hermes caps memory at about 2,200 characters, and an imported one can be
well over that. Remove it if so; `SOUL.md` carries the standing rules. Copies are better than
`skills.external_dirs`, because Hermes edits skills in place wherever they
live, which would change the Claude originals. Refresh with
`hermes -p <profile> import-agent --sync`.

## Safety hook (optional)

If you have a Bash guard hook for Claude Code, Hermes can reuse it as-is:
Hermes shell hooks take the same exit-2-means-block convention and the same
`tool_input.command` payload.

```yaml
hooks:
  pre_tool_call:
    - matcher: "terminal"
      command: "<path to your guard script>"
      timeout: 10
```

The profile needs one approval: `hermes -p <profile> --accept-hooks`. Check
with `hermes -p <profile> hooks doctor`.

## Reading the switchboard from Claude Code (optional)

Claude Code can read the Hermes profile's conversations. Run this under
the Claude config dir your orchestrators use (prefix it with
`CLAUDE_CONFIG_DIR=<dir>` if that is not the default):

```
claude mcp add --scope user hermes -- hermes -p <profile> mcp serve
```

## Permissions

Each repo an orchestrator works in needs, in its gitignored
`.claude/settings.local.json`, allow rules for `Bash(gh pr create:*)`,
`Bash(gh pr edit:*)` and `Bash(git push origin fanout/*)` style pushes from
worktrees, plus `Bash(gh pr merge:*)` only on your own repos (owners in
`LEGWORK_OWN_OWNERS`) when the roadmap says to merge. Add deny rules for
`git push origin main`, `git push --force` and `git push -f`. Without the
allow rules the permission classifier stalls every run waiting on an
approval. Never commit these rules on a shared repo.

## Messaging

The switchboard thread can live in a terminal pane, or in any channel your
Hermes gateway supports. Pick one your organisation is comfortable with.
