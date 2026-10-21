# Switchboard setup

How to set up the Claude Code switchboard on a Mac or on Windows. The
switchboard is one Claude Code session in a Herdr pane named `switchboard`.
It watches every orchestrator, puts each `DECISION` in front of you, relays
your answer into the right pane, and cycles context. If you would rather
relay by hand, skip to [Manual mode](#manual-mode).

In the commands below, `<legwork>` is the legwork repo (where `config`
lives) and `<checkout>` is the legwork checkout the scripts run from. They
are usually the same folder. `<claude dir>` is your Claude config dir:
`$CLAUDE_CONFIG_DIR` if you set it, else `~/.claude`.

## What you need

- **Herdr**, with `herdr` on your PATH. Install it from
  [herdr.dev](https://herdr.dev). The switchboard finds, reads, prompts
  and opens panes through it.
- **Claude Code**, with `claude` on your PATH.
- **Python 3.9 or newer.** On a Mac that is `python3`. On Windows it is
  `python`: `python3` there is usually the Microsoft Store stub.
- **No status line needed.** The switchboard checks its own context, and
  each orchestrator's, with `orchestration/bin/switchboard-context`, which
  reads the session's transcript under `<claude dir>/projects/`. It cycles
  itself past 300,000 tokens or 60% of the window. It learns the window
  from a 1M model named in the transcript, or from `LEGWORK_CONTEXT_WINDOW`
  (see Settings). Without either it goes by the 300,000 limit alone.
- **The legwork repo trusted in Claude Code.** Start `claude` in
  `<legwork>` once and answer the "trust this folder" question. The
  switchboard runs there, and the health watcher restarts it there.
- On a Mac, for the banners: `terminal-notifier`, from Homebrew
  (`brew install terminal-notifier`).

## Install

From the checkout:

```
python3 orchestration/install.py --with switchboard,switchboard-health,pane-notify --dry-run
python3 orchestration/install.py --with switchboard,switchboard-health,pane-notify
```

On Windows, run it as `python orchestration/install.py ...`. Add
`--set LEGWORK_SWITCHBOARD=claude` if your config says `manual`;
`claude` is the default. Pass `--legwork-dir <legwork>` when the legwork
repo is not this checkout, and `--claude-dir <claude dir>` for a Claude
config dir that is not the default. `/onboard` asks about each piece and
runs the same command for you.

| Piece | What it installs |
|---|---|
| `switchboard` | The skills `switchboard-protocol` (orchestrators follow it), `switchboard` (the `/switchboard` command) and `roadmap-planning`, into `<claude dir>/skills/`, and the switchboard session's allow rules (below). It also sets `LEGWORK_ORCHESTRATION=1` in `config` |
| `switchboard-health` | Sets `LEGWORK_SWITCHBOARD_HEALTH=1` in `config`, after checking that `herdr` and the system scheduler (`launchctl` on a Mac, `schtasks` on Windows) are on PATH. It registers no job: the switchboard runs `switchboard-health-ctl on` itself |
| `pane-notify` | `Stop` and `Notification` hooks in `<claude dir>/settings.json` that run `orchestration/bin/pane-notify`, with a timeout of 10 seconds |

The scripts stay in `<checkout>/orchestration/bin/`. The installed skills
call them by their full path, and on Windows with the interpreter in front.
On Linux, `switchboard-health` and `pane-notify` are skipped with a
message. Re-run the same command after a `git pull` to refresh the copies.

## Settings

All in `<legwork>/config`. See [CONFIG.md](../../CONFIG.md) for every
orchestration setting.

- `LEGWORK_SESSION_ARGS`: the arguments `claude` starts with when the
  switchboard opens an orchestrator pane, and when the health watcher
  restarts the switchboard. Default `--model opus --effort high`.
- `LEGWORK_SWITCHBOARD_DIR`: the switchboard's own folder. It holds the
  state file, `state.md`, and the health watcher's files. Default
  `<claude dir>/switchboard`. `SWITCHBOARD_STATE` in the environment names
  the state file itself instead.
- `LEGWORK_CONTEXT_WINDOW`: the switchboard session's context window, as a
  number of tokens or with `k` or `m` after it (`200k`, `1m`). Unset, the
  window comes from a 1M model named in the transcript, else it is unknown
  and only the 300,000 limit counts. On a 200k window set it to `200k`, so
  the switchboard cycles at 60% (120,000 tokens), before Claude Code
  compacts the session.

The scripts find `<legwork>/config` through the environment. For each
setting they look at the environment variable first, then the file named by
`$LEGWORK_CONFIG`, then `$LEGWORK_DIR/config`, then `config` in the
checkout. So when `<legwork>` is not the checkout, set
`LEGWORK_DIR=<legwork>` in the environment the switchboard and
`switchboard-health-ctl` run in. On a Mac, `export` it in your shell
profile. On Windows, run `setx LEGWORK_DIR <legwork>` or add it to your
user environment variables, then restart Herdr, since a variable set this
way reaches only programs started after it. Without it the scripts read
the checkout's `config`, and the health watcher restarts the switchboard in
the checkout. `LEGWORK_CONFIG=<file>` points the scripts at a config file
instead; set `LEGWORK_DIR` in that file too, for the restart folder.

## Allow rules the switchboard session needs

Without allow rules the switchboard stalls on a permission prompt. The
`switchboard` piece writes them into `<legwork>/.claude/settings.local.json`,
unless `LEGWORK_SWITCHBOARD` is `manual`. It merges them with the rules
already there: each rule goes in once, and your own rules stay as they are.
It keeps a record of what it wrote in
`<legwork>/.legwork/switchboard-allow-rules.json`, so a re-run after you
move the checkout, the switchboard's folder or your repos replaces its old
rules instead of adding new ones beside them. legwork's `.gitignore` keeps
both files out of git; never commit them. `--dry-run` prints every rule it
would write. A `settings.local.json` that is not valid JSON is left alone,
with one line saying so.

What it writes:

- A Bash rule for each script the switchboard runs, in the exact form the
  installed skill types it: `switchboard-wait`, `switchboard-answer`,
  `switchboard-state`, `switchboard-health-ctl` and `switchboard-context`,
  each followed by `:*`, and `usage-guard --check` on its own. On a Mac
  that is the script's absolute path. On Windows the interpreter's path
  comes first, as the skill writes each line for Git Bash, the shell
  Claude Code's Bash tool uses there.
- `herdr agent list`, `herdr agent read` and `herdr pane read`, to find
  panes and read them; `herdr agent prompt`, to relay and cycle;
  `herdr tab create` and `herdr agent start`, to open an orchestrator pane
  for "run <project>", and `herdr pane send-text`, to answer a shell
  question there; `herdr pane current` and `herdr agent rename`, so the
  switchboard can give its own pane the name `switchboard`.
- `grep` and `tail`, to read the switchboard files. A Bash rule cannot be
  limited to one file, so they are allowed for any path. They only read.
- `Read(<repos>/**/.legwork/**)` for each folder your project repos sit in,
  from the `repo:` lines in `<legwork>/projects/` that name a folder on
  this machine, and `Read(<switchboard dir>/**)` for
  `LEGWORK_SWITCHBOARD_DIR`. A folder under your home is written `~/...`,
  any other absolute path with a leading `//`, and a Windows drive in
  POSIX form, so drive E is `//e/...`. With no project repo on this
  machine yet, the installer says so; run it again once there is one.
- One deny rule, `Edit(<switchboard dir>/state.md)`: the state file changes
  only through `switchboard-state`. There is no `Edit` allow rule. An
  `Edit` rule covers every tool that writes a file; Claude Code ignores
  `Write(path)` rules.

Push notifications use Claude Code's `PushNotification` tool, which needs
no rule.

Each repo an orchestrator works in needs its own rules too. They are in
[first-run.md](first-run.md), under Permissions.

## Starting the switchboard

Run one switchboard per machine. It watches every project from the one
session.

1. Open a Herdr pane in `<legwork>` and start Claude Code there with the
   session arguments, the value of `LEGWORK_SESSION_ARGS` (unset:
   `--model opus --effort high`):
   ```
   claude <LEGWORK_SESSION_ARGS>
   ```
2. Type `/switchboard <project>`. Name several projects to watch them all.
   A name is a legwork project or an orchestrator's session name. The skill
   loads only from this command, so "check in" or "watch" in another
   session never starts a switchboard.
3. It names its own pane `switchboard` (`herdr agent rename`), adds the
   projects to its state file, switches the health watcher on when it is
   installed, and starts waiting.

To open the pane from a script instead, start Claude Code in a tab of its
own:

```
herdr tab create --cwd <legwork> --env DISABLE_AUTO_UPDATE=true --label switchboard
herdr agent start switchboard --kind claude --pane <pane id> -- <LEGWORK_SESSION_ARGS>
```

Take the pane id from the JSON the first command prints. Put the value of
`LEGWORK_SESSION_ARGS` after the `--` (unset: `--model opus --effort
high`), the same arguments the health watcher restarts it with. Add
`--env CLAUDE_CONFIG_DIR=<claude dir>` to the first line when your Claude
config dir is not the default. `DISABLE_AUTO_UPDATE=true` stops oh-my-zsh
asking to update when the shell starts; that question eats the first
letter `herdr agent start` types. It does nothing in other shells. If the
start fails with `agent_not_ready`, the folder is not trusted yet: answer
the question in the pane and start again.

After a `/clear`, a crash or a restart, type `/switchboard resume` (or
`/switchboard` alone). It rebuilds from the state file and the repos'
`.legwork/switchboard.md` files, never from memory.

## Answering

The switchboard waits with a foreground `switchboard-wait`, for up to 590
seconds at a time. It does not see what you type until the wait returns. So
press Esc first, then type. Esc stops the wait within about a second and
nothing is lost: the wait keeps a cursor, and the switchboard checks the
state and starts a new wait after any interrupt.

- Answer a decision with its letter, such as `smoke-1: A`, or in your own
  words. `switchboard-answer` checks the id is open, sends
  `SWITCHBOARD ANSWER <id>: <answer>` to the orchestrator's pane and writes
  the `ANSWERED` line.
- To delegate, say what the delegation covers. It is recorded as a
  `<project>-gN GRANT` entry.
- `check in` lists open decisions, updates since the last check-in and
  anything wrong. `watch <project>` and `run <project>` add a project.

It sends a push notification for an open `DECISION`, a `DONE`, a blocked
orchestrator pane and a weekly usage figure at 99%. Nothing else notifies.

## The state file

`state.md` in `LEGWORK_SWITCHBOARD_DIR` is the switchboard's memory: the
projects it watches (`<project> <repo> <pane id>`), when each run started,
the last check-in per project, and a final `CYCLE <date>` line while it is
asking to be cycled. Only `switchboard-state` writes it. A `CYCLE` or
`DONE` dated before a run's start is left out, so an earlier run's `DONE`
never ends a new one. To read it by hand:

```
orchestration/bin/switchboard-state show
```

On Windows, put `python` in front (`python orchestration/bin/switchboard-state show`).

## The health watcher

`orchestration/bin/switchboard-health` is a script, not a model. While it is
switched on it runs once at once and then every 5 minutes. Each pass:

- checks the `switchboard` pane is there and not blocked;
- cycles the switchboard when its state file ends in `CYCLE` and it is
  idle: `/clear`, `/rename switchboard`, `/switchboard resume`;
- prompts the switchboard when a wait has stopped, or when an orchestrator
  is blocked, idle with work outstanding, or has not moved for 60 minutes;
- restarts a dead switchboard, at most once an hour, in `<legwork>` with
  `LEGWORK_SESSION_ARGS`. It does not restart one whose state file is
  missing or watches nothing, because you may have closed it on purpose.

It prompts only an idle switchboard, at most one prompt a pass, and never
sends Esc. Each alert fires once until its condition clears.

### On and off

With `LEGWORK_SWITCHBOARD_HEALTH=1`, the switchboard switches the watcher on
when it starts watching a project and off when its last project writes
`DONE` or you tell it to stop. The watcher also switches itself off after
two passes in a row (10 minutes) with nothing to watch, which covers a
`/clear` or a closed pane. It is off after every sign-in or restart, until
the switchboard switches it on again.

By hand, from the checkout:

```
orchestration/bin/switchboard-health-ctl on
orchestration/bin/switchboard-health-ctl status
orchestration/bin/switchboard-health-ctl off
```

On Windows, put `python` in front of each line. Run them with the same
`CLAUDE_CONFIG_DIR` and legwork settings as the switchboard, since the job
carries them. `on` first runs `switchboard-health --once --dry-run` and
shows its output, then schedules the job. The dry run sends, starts and
writes nothing, so read it before you rely on the watcher. `on` when it is
already on, or `off` when it is already off, says so and does nothing.
`status` says whether it is on and describes the last pass. `--state-dir
DIR` puts the job's files in another folder than `LEGWORK_SWITCHBOARD_DIR`,
and the watcher then also looks for the switchboard's `state.md` in DIR.
So pass it only with `SWITCHBOARD_STATE` set to the switchboard's state
file; otherwise the watcher finds no state file, checks nothing, and
switches itself off after two passes.

- **Mac:** a launchd agent, `dev.legwork.switchboard-health`. `on` renders
  `orchestration/launchd/dev.legwork.switchboard-health.plist.template`
  into the state folder, never into `~/Library/LaunchAgents`, and loads it
  with `launchctl bootstrap`, so it goes at logout. `claude` is looked for
  on PATH, then under `~/.nvm/versions/node/*/bin`, and `herdr` and
  `terminal-notifier` on PATH; their folders go on the job's PATH, since a
  launch agent does not get your shell's. If an old copy of the plist is
  in `~/Library/LaunchAgents`, `on` and `status` warn that it loads at
  login and print the command to remove it. The script never deletes it.
- **Windows:** a Task Scheduler task, `dev.legwork.switchboard-health`,
  that runs only while you are signed in, under the windowless
  `pythonw.exe` when there is one. `on` writes the task's XML into the
  state folder, registers it with `schtasks /Create /XML` and starts its
  first pass with `schtasks /Run`. The task carries the sign-in it was
  switched on in, and a pass in a later sign-in switches it off. `herdr`
  and `claude` must be on your own user PATH.

Each pass appends one JSON line to `health.jsonl` in the state folder. The
job's own output goes to `health.out.log` and `health.err.log` beside it,
and the alert state is in `health-state.json`.

When something needs you that the switchboard cannot handle (the
switchboard blocked, a cycle that did not take, a restart that failed, or
an orchestrator with no change for 60 minutes), the watcher shows a banner:
`terminal-notifier` on a Mac, a Windows toast through Windows PowerShell on
Windows. On a Mac, allow
`terminal-notifier` in System Settings, Notifications. Until a banner can
be shown, it is logged as failed and tried again on the next pass.

## Pane notifications

`pane-notify` is a `Stop` and `Notification` hook. It shows a banner and
plays a sound from orchestrator panes and normal Claude sessions only. The
`switchboard` pane, `fw-<slug>` worker panes, subagents and `claude -p`
stay quiet. Herdr can mute its toasts and sounds per agent kind only, not
per pane, so switch its own off. Otherwise each notice comes twice. In
Herdr's config, `~/.config/herdr/config.toml` on a Mac and
`%APPDATA%\herdr\config.toml` on Windows:

```toml
[ui.toast]
delivery = "off"

[ui.sound.agents]
claude = "off"
```

Then run `herdr server reload-config`.

- **Mac:** the banner comes from `terminal-notifier`
  (`brew install terminal-notifier`, then allow it in System Settings,
  Notifications), found on PATH, else at `/opt/homebrew/bin`. The sound is
  Herdr's chime from `~/.config/herdr/sounds`.
- **Windows:** the banner is a toast through Windows PowerShell, so it
  shows under Windows PowerShell in your notification settings. The sound
  is a built-in Windows sound.

A Stop shows `<name>: done` with the first line of the last message. A
permission prompt or a question shows `<name>: needs you`. `<name>` is the
pane's label, else its title, else its window title, else its folder's
name. To check it, end a turn in a
normal pane and look for the done banner. To switch it off, remove the two
`pane-notify` hook entries from `<claude dir>/settings.json` and turn
Herdr's own toasts and sounds back on.

## Manual mode

With `LEGWORK_SWITCHBOARD=manual`, you are the switchboard. Install with:

```
python3 orchestration/install.py --set LEGWORK_SWITCHBOARD=manual --with switchboard
```

On Windows, run it as `python orchestration/install.py ...`. The
`switchboard` piece then installs the `switchboard-protocol` and
`roadmap-planning` skills and leaves out the `switchboard` skill.
`switchboard-health` is skipped, since no switchboard session runs for it
to watch. `pane-notify` works in either mode.

To relay by hand, run the wait in a spare terminal from the checkout:

```
orchestration/bin/switchboard-wait --timeout 1800 <repo>:<pane>
orchestration/bin/switchboard-wait --status <repo>:none
```

On Windows, put `python` in front of each line. The wait returns when there
is something for you; `<repo>:none` watches the file only, without Herdr.
Read the entry in `<repo>/.legwork/switchboard.md`, type your answer into
the orchestrator's pane, and start the wait again. The orchestrator writes
the `ANSWERED` line itself. On a `CYCLE`, type `/clear`,
`/rename orch-<project>` and the entry's `Resume:` line into its pane. A
planning session ends with a `/fanout` prompt for you to paste into a new
orchestrator pane.

## Next

- [smoke-test.md](smoke-test.md): a 15 minute plumbing test before a real
  run.
- [first-run.md](first-run.md): the first real run on a project.
