# Orchestration

The optional layer past the manual loop: one long-running orchestrator per
project, and one switchboard over all of them.

Each project gets an orchestrator: a Claude Code session, ideally in a Herdr
pane titled `orch-<project>`. It runs for hours, hands work out to
subagents or worker panes, and decides everything it is entitled to decide.
Only key updates and decisions with knock-on effects come up to you. The
switchboard reads what every orchestrator writes, puts the open decisions in
front of you one at a time, relays your answers back into the right pane,
and cycles each orchestrator's context when it reaches a clean pause. The
switchboard is a Claude Code session in a Herdr pane named `switchboard`,
or you, by hand. An optional health watcher, a script and not a model,
checks on the switchboard every 5 minutes while it is switched on, on a Mac
and on Windows.

The state lives in files, not in anyone's context window:
`.legwork/switchboard.md` in each repo, and `state.md` in
`LEGWORK_SWITCHBOARD_DIR` for the switchboard itself. So the switchboard
can `/clear`, or an orchestrator can, and nothing is lost.

Every piece is optional. Run `/onboard` in a legwork checkout and it asks
which ones you want, then installs only those with `install.py`. To set up
the Claude Code switchboard by hand, with its allow rules, the health
watcher and pane notifications, see
[docs/switchboard-setup.md](docs/switchboard-setup.md).

## What's here

| Path | What it is |
|---|---|
| `commands/ship.md`, `commands/bugbot.md` | `/ship` lands the working tree as a PR and runs the Bugbot loop; `/bugbot` is that loop on its own |
| `commands/fanout.md`, `skills/fanout/` | `/fanout <project>`: load the queue, plan PR-sized tasks, dispatch them to panes or subagents, land each PR through the gate and Bugbot, clean up, wrap |
| `skills/switchboard-protocol/` | The skill orchestrators follow: when to escalate, the entry format, context cycling, the usage budget, the quality gate |
| `skills/switchboard/` | The skill for the switchboard session itself (`/switchboard <project>` or `/switchboard resume`): watching, relaying answers, cycling, opening orchestrator panes. A template; `install.py` fills in your paths |
| `skills/roadmap-planning/` | The planning session that turns a feature into a roadmap and queues the `/fanout` prompt the switchboard starts, or that you paste with a manual switchboard. Installed in both modes |
| `bin/switchboard-health`, `bin/switchboard-health-ctl` | The health watcher (macOS launchd, Windows Task Scheduler): checks the switchboard every 5 minutes, cycles its context and restarts it. The switchboard switches it on and off with the ctl (`on`, `off`, `status`); it is off after every sign-in |
| `launchd/dev.legwork.switchboard-health.plist.template` | The macOS launch agent `switchboard-health-ctl on` renders into the switchboard's folder, never into `~/Library/LaunchAgents`. On Windows the ctl writes a Task Scheduler task instead |
| `bin/pane-notify` | A Stop and Notification hook: a banner and a sound from orchestrators and normal sessions, silence from workers and the switchboard |
| `bin/switchboard-wait` | The wait behind watch mode. On start it reports open decisions, a trailing `CYCLE` or `DONE` and panes already stopped, then returns when a watched repo gets a new `DECISION`, `CYCLE` or `DONE`, or a pane goes blocked or idle. `REPO:none` watches the file only, for a setup without Herdr |
| `bin/switchboard-context` | How much context a Claude Code session is using (the switchboard's own, or an orchestrator's by `--session` or `--cwd`), read from its transcript, so no status line is needed. One line out; exit 0 carry on, 3 cycle now, 2 cannot tell. The window comes from `LEGWORK_CONTEXT_WINDOW` or a 1M model in the transcript, else only the 300,000 limit counts |
| `bin/switchboard-state` | The only writer of the switchboard's state file (`LEGWORK_SWITCHBOARD_DIR/state.md`): `add [--run]`, `pane`, `remove`, `checkin`, `cycle`, `uncycle`, `show`. `add --run` records when a run started, so `switchboard-wait` leaves out the previous run's `CYCLE` or `DONE` |
| `bin/bugbot-wait` | Triggers and waits for a Cursor Bugbot verdict on a PR, for owners in `LEGWORK_BUGBOT_OWNERS` |
| `bin/switchboard-answer` | Relays the user's answer to an open `DECISION` (or, with `--grant`, a delegation) into the orchestrator's pane with `herdr agent prompt`, then writes the `ANSWERED` or `<project>-gN GRANT` entry itself. It refuses a closed or unknown id, a newline, or a failed send, and then writes nothing |
| `agents/codex.md` | The `codex` subagent: runs OpenAI's Codex CLI on one scoped task, for the cross-model review fanout and the quality gate ask for. The caller picks model and effort with `codex-model:` and `codex-effort:` lines; the description carries the routing guide |
| `bin/usage-guard` | A hook that tells a long run to pause at a safe point before a usage limit, from the ccstatusline usage cache |
| `install.py` | Installs the pieces you choose, writes their settings into `config`, and writes the switchboard session's allow rules into the legwork repo's `.claude/settings.local.json` |
| `docs/switchboard-setup.md` | Setting up the Claude Code switchboard on a Mac or on Windows: what it needs, the install, the allow rules, starting it, the health watcher, pane notifications, manual mode |
| `docs/smoke-test.md` | A 15 minute plumbing test before a real run |
| `docs/first-run.md` | The first real run on a project |

## Settings

All in the legwork `config` file, documented in `config.example` under
"Orchestration": who the switchboard is (`LEGWORK_SWITCHBOARD`, `claude` or
`manual`), whether the health watcher is installed
(`LEGWORK_SWITCHBOARD_HEALTH`), the arguments new sessions start with
(`LEGWORK_SESSION_ARGS`), which GitHub owners are yours
(`LEGWORK_OWN_OWNERS`), which have Bugbot (`LEGWORK_BUGBOT_OWNERS`), the
Linear team, and the usage cache.
Rules every worker follows (authorship, attribution, spelling, style) live
in `house-rules.md` beside `config`; see `house-rules.example.md`.

## How it flows

```
 User --- 1. planning session, roadmap-planning ---> a queued /fanout prompt
  ^ | ^                                                  run <project>  |
  | | : banner if the switchboard is stuck ...............              |
  | | 3. press Esc, then answer with a letter            :              |
  | |    (a push comes for a DECISION, a DONE or a       :              |
  | |    blocked pane)                                   :              |
  | v                                                    :              |
 +-----------------------------+  on, off   +-----------------------+   |
 | switchboard                 |<---------->| 5. switchboard-health |   |
 | a Claude Code session in    |  prompts,  | a script, every 5 min |   |
 | the Herdr pane switchboard; |  cycles,   | while it is on        |   |
 | its own state in state.md   |  restarts  +-----------------------+   |
 +-----------------------------+                                        |
    |  ^              |                                                 |
    |  | wakes on a   | switchboard-answer: SWITCHBOARD ANSWER,         |
    |  | new entry or | and 4. on a CYCLE: /clear, /rename,             |
    |  | pane change  |    then the Resume line                         |
    v  |              v                                                 v
 switchboard-  +---------------------------------------------------------+
 wait          | 2. orch-<project>: one Claude Code orchestrator per     |
    ^          |    project, running the queued /fanout prompt           |
    | watches  +---------------------------------------------------------+
    |                  | writes entries          | dispatches
    |                  v                         v
 +----------------------------+          fw-<slug> worker panes and
 | .legwork/switchboard.md:   |          subagents --> pull requests:
 | UPDATE, DECISION, DECIDED, |          gate, review, merge
 | CYCLE, DONE, and ANSWERED  |
 | and GRANT                  |
 +----------------------------+
```

The numbers match the steps below.

1. **Planning session, with you.** Grill the feature with `/grill-with-docs`
   (from the mattpocock-skills plugin), follow `roadmap-planning`, write
   the plan as roadmap items, `/wrap`. It ends with a queued `/fanout`
   prompt.
2. **Long run, without you.** The orchestrator follows `switchboard-protocol`
   and usually runs `/fanout`. It writes `UPDATE`, `DECISION`, `DECIDED`,
   `CYCLE` and `DONE` entries as it goes.
3. **Switchboard.** In a Herdr pane, start `claude` in the legwork repo
   and run `/switchboard <project>`; press Esc, then answer decisions with a
   letter. `switchboard-answer` relays and records each answer.
   [docs/switchboard-setup.md](docs/switchboard-setup.md) has the start
   command and the allow rules `install.py` writes. By hand: `bin/switchboard-wait --status
   <repo>:none` shows what is open; type the answer into the pane. On
   Windows, put `python` in front (`python bin/switchboard-wait ...`): the
   scripts' first line asks for `python3`, which there is usually the
   Microsoft Store stub.
4. **Context cycling.** Past 300,000 tokens, or 60% of a smaller window, the
   orchestrator finds a clean pause, runs `/wrap`, writes `CYCLE` and stops.
   The switchboard sends `/clear`, `/rename orch-<project>` and the resume
   command. The switchboard cycles itself the same way, and the health
   watcher sends its `/clear`, `/rename switchboard` and
   `/switchboard resume`. Without the watcher, you send those three.
5. **Health watcher.** Optional, on a Mac and on Windows. The switchboard
   switches it on and off with its watch list. Every 5 minutes it prompts
   the switchboard when a wait has stopped or an orchestrator needs a look,
   restarts a dead switchboard at most once an hour, and shows you a banner
   when the switchboard is stuck.

## Herdr

Strongly recommended. With [Herdr](https://herdr.dev), workers run in panes
you can watch and steer, and the switchboard can open, cycle and resume
orchestrators. Without it, `/fanout` dispatches subagents only, and the
switchboard is the file plus you. `install.py --with herdr-skill` saves
Herdr's own skill (`herdr --skill`) where Claude Code finds it.

## Updating

The copies here are the source of truth. Re-run `install.py` with the same
pieces after a `git pull` to refresh the installed copies. A piece legwork
no longer ships is skipped with one line, and the rest still install.
