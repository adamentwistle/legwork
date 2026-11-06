# The orchestrator moved to Orchi

Legwork used to ship an orchestrator in `orchestration/`. It is now part of
[Orchi](https://github.com/adamentwistle/orchi-build), which works from a
legwork tracker. If you installed it from an older checkout of this repo,
the pieces below are still on your machine, and they call scripts this
checkout no longer has. Remove the ones you have. Orchi's setup installs its
own copies if you move to it.

## In your Claude config dir

That is `$CLAUDE_CONFIG_DIR` if set, else `~/.claude`.

- `commands/ship.md`, `commands/bugbot.md` and `commands/fanout.md`
- `skills/fanout`, `skills/switchboard-protocol`, `skills/switchboard` and
  `skills/roadmap-planning`
- `agents/codex.md`

In its `settings.json`, the hooks whose command runs
`orchestration/bin/usage-guard` (on `UserPromptSubmit` and `PostToolUse`) or
`orchestration/bin/pane-notify` (on `Stop` and `Notification`). They fail on
every prompt until they go.

## The health watcher

Only if the switchboard switched it on. It runs every 5 minutes until you
sign out.

- macOS: `launchctl bootout gui/$(id -u)/dev.legwork.switchboard-health`.
  If `~/Library/LaunchAgents/dev.legwork.switchboard-health.plist` exists,
  delete it too, or it loads again at login.
- Windows: `schtasks /Delete /TN dev.legwork.switchboard-health /F`.

## In the legwork repo

- The switchboard session's allow rules in `.claude/settings.local.json`.
- The orchestrator's settings in `config`, such as `LEGWORK_SWITCHBOARD`.
  Legwork reads none of them, and `./install.sh` keeps every line it does
  not own, so delete them by hand.
