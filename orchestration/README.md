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
switchboard is a Hermes thread, or you, by hand.

The state lives in a file in each repo, `.legwork/switchboard.md`, not in
anyone's context window. So the switchboard thread can reset, or an
orchestrator can `/clear`, and nothing is lost.

Every piece is optional. Run `/onboard` in a legwork checkout and it asks
which ones you want, then installs only those with `install.py`.

## What's here

| Path | What it is |
|---|---|
| `commands/ship.md`, `commands/bugbot.md` | `/ship` lands the working tree as a PR and runs the Bugbot loop; `/bugbot` is that loop on its own |
| `commands/fanout.md`, `skills/fanout/` | `/fanout <project>`: load the queue, plan PR-sized tasks, dispatch them to panes or subagents, land each PR through the gate and Bugbot, clean up, wrap |
| `skills/switchboard-protocol/` | The skill orchestrators follow: when to escalate, the entry format, context cycling, the usage budget, the quality gate |
| `hermes/switchboard/` | The Hermes skill for the switchboard itself: check-ins, relaying answers, cycling, watching, planning sessions. A template; `install.py` fills in your paths |
| `bin/switchboard-wait` | The wait behind watch mode. On start it reports open decisions, a trailing `CYCLE` or `DONE` and panes already stopped, then returns when a watched repo gets a new `DECISION`, `CYCLE` or `DONE`, or a pane goes blocked or idle. `REPO:none` watches the file only, for a setup without Herdr |
| `bin/bugbot-wait` | Triggers and waits for a Cursor Bugbot verdict on a PR, for owners in `LEGWORK_BUGBOT_OWNERS` |
| `bin/usage-guard` | A hook that tells a long run to pause at a safe point before a usage limit, from the ccstatusline usage cache |
| `install.py` | Installs the pieces you choose and writes their settings into `config` |
| `docs/hermes-setup.md` | Setting up Hermes as the switchboard |
| `docs/smoke-test.md` | A 15 minute plumbing test before a real run |
| `docs/first-run.md` | The first real run on a project |

## Settings

All in the legwork `config` file, documented in `config.example` under
"Orchestration": who the switchboard is (`LEGWORK_SWITCHBOARD`), the
Hermes profile, which GitHub owners are yours (`LEGWORK_OWN_OWNERS`), which
have Bugbot (`LEGWORK_BUGBOT_OWNERS`), the Linear team, and the usage cache.
Rules every worker follows (authorship, attribution, spelling, style) live
in `house-rules.md` beside `config`; see `house-rules.example.md`.

## How it flows

1. **Planning session, with you.** Grill the feature with `/grill-with-docs`
   (from the mattpocock-skills plugin),
   write the plan as roadmap items, `/wrap`.
2. **Long run, without you.** The orchestrator follows `switchboard-protocol`
   and usually runs `/fanout`. It writes `UPDATE`, `DECISION`, `DECIDED`,
   `CYCLE` and `DONE` entries as it goes.
3. **Switchboard.** In Hermes, say "watch <project>" or "check in", and
   answer decisions with a letter. By hand: `bin/switchboard-wait --status
   <repo>:none` shows what is open; type the answer into the pane.
4. **Context cycling.** Past 300,000 tokens, or 60% of a smaller window, the
   orchestrator finds a clean pause, runs `/wrap`, writes `CYCLE` and stops.
   The switchboard sends `/clear`, `/rename orch-<project>` and the resume
   command.

## Herdr

Strongly recommended. With [Herdr](https://herdr.dev), workers run in panes
you can watch and steer, and the switchboard can open, cycle and resume
orchestrators. Without it, `/fanout` dispatches subagents only, and the
switchboard is the file plus you. `install.py --with herdr-skill` saves
Herdr's own skill (`herdr --skill`) where Claude Code finds it.

## Updating

The copies here are the source of truth. Re-run `install.py` with the same
pieces after a `git pull` to refresh the installed copies. Hermes edits its
own skills when it learns something, so diff the installed Hermes copy
against `hermes/switchboard/SKILL.md` after a run and bring anything useful
back here.
