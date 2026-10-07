---
name: fanout
description: Orchestrate an unattended multi-task session for one legwork project. Loads everything queued (roadmaps, next prompt, open PRs), plans PR-sized tasks, dispatches each to a Herdr pane or a subagent at the orchestrator's judgement, runs the gate and the Bugbot loop on every PR, rebases siblings when main moves, cleans up panes and worktrees, and wraps. Use for /fanout, "overnight session", "work through the roadmap", "assign tasks in parallel", or any request to run several tasks to PRs without supervision.
---

# fanout

You are the orchestrator for an unattended session. The user is not
watching and will not answer questions. Every decision is yours; record
it, do not ask. The deliverable is a set of pull requests that are
gate-green and Bugbot-clean, a clean workspace, and a tracker entry that
says exactly what landed and what did not.

`<legwork>` below is the legwork repo: `$LEGWORK_DIR` if set, otherwise
`~/legwork`. Its settings are KEY=VALUE lines in `<legwork>/config`; a real
environment variable wins over the file. The ones this skill reads are
`LEGWORK_OWN_OWNERS` (the GitHub owners whose repos are the user's own),
`LEGWORK_BUGBOT_OWNERS` (the owners with Cursor Bugbot installed) and
`LEGWORK_PR_SKILL` (the skill that writes the user's PR bodies, if any).

Building blocks you already have: `/bugbot` (review loop), `/wrap`
(tracker close-out), the `LEGWORK_PR_SKILL` skill when set (PR body), the
`herdr` skill (pane control and the
pane-or-subagent table; `herdr --skill` prints it if it is not installed),
and `{{LEGWORK_BIN}}/bugbot-wait` (not on PATH; the installer wrote the
full path here, use it as written). Do not re-derive any of them.

## 0. Arguments and flags

`/fanout <project> [--merge] [--low-priority] [--max N] [--only "a, b"]`

- `--merge`: merge PRs that come back clean, in dependency order. Without it,
  PRs are left open, rebased on main and Bugbot-clean, for the user to merge.
  Default is no merge: merging is outward-facing.
- `--low-priority`: send `/low-priority` to every pane before its brief.
  Send it once, wait for the agent to go idle, then send the brief. The
  mode switch consumes the turn, so a brief sent first is lost.
- `--max N`: cap the number of tasks (default 6).
- `--only`: restrict to the named roadmap items.

## 1. Load everything (read only)

1. Tracker: `<legwork>/projects/<project>.md`.
   Read in full: frontmatter (`repo`, `status`, `blocked_on`), `## Vision`,
   `## Next prompt`, every `## Roadmap*` section, and the last ten log
   entries. Roadmap sections are newest first by convention; an item that a
   later log entry says shipped is done, whatever the roadmap says.
2. Repo: `git -C <repo> fetch origin`, `git status --short`, `git log
   origin/main --oneline -15`, `gh pr list --state open --json
   number,title,headRefName,isDraft,reviewDecision`, and for each open PR
   `{{LEGWORK_BIN}}/bugbot-wait <n> --after 2000-01-01T00:00:00Z --timeout 1`
   to learn its last verdict without waiting. `NO_BUGBOT` (exit 4) means
   the repo's owner has no Bugbot (not in `LEGWORK_BUGBOT_OWNERS`): skip the
   Bugbot loop for the whole run and say so in the log.
3. Repo instructions: `CLAUDE.md`, `docs/claude/` if present, `PROJECT.md`
   or `README.md`. Note the gate command (`scripts/pr-gate.sh`,
   `scripts/secret-sweep.sh`, `make ...`, `go test ./...`, `npm test`).
4. House rules: `<legwork>/house-rules.md`. Every list item in it goes into
   every brief below, verbatim. If the file does not exist, say so in the
   state file and in the wrap log: the workers then have no house rules.
5. Write `.legwork/fanout-state.md` in the repo (gitignored): the plan
   below, one line per task, updated after every state change. This is the
   handoff file if the orchestrator's context runs out.

If `blocked_on` is set, or the next prompt opens with `Human action, not a
Claude session.` or `DECISION NEEDED`, stop here and print it. Nothing else
in this skill overrides that.

## 2. Plan

Turn the queue into tasks. One task = one PR, small enough for a single
worker to finish in one sitting with a verifiable finish line. Order:

1. The `## Next prompt` block, verbatim, is task 1 unless `--only` excludes it.
2. Then roadmap items, newest roadmap section first, skipping items the log
   shows as shipped. Skip items that need a human (credentials, a decision
   the user has explicitly reserved, external accounts, anything that says
   to ask the user first) and list them in the final report instead.
3. Open PRs with Bugbot findings are tasks too: "fix findings on PR N".

Mark each task `parallel` or `after <task>`. Two tasks are parallel only
when they touch disjoint files or packages. When unsure, serialise;
conflict resolution costs more than waiting.

Write one brief per task to `<scratchpad>/briefs/<slug>.md` with this
skeleton and nothing else:

```
# <slug>
Repo: <path>   Branch: fanout/<slug>   Base: origin/main   Model: <model>   Effort: <level>
Context: <3-6 lines: what the project is, why this task, what is already done>
Task: <the work, concrete>
Done when: <verifiable finish line>
Gate: <exact command(s); must print PASS or exit 0>
Rules: house rules ("me" and "I" in them mean the user whose git and gh
  identity this repo uses): <every list item of house-rules.md, verbatim>;
  PR to main from this branch only, never push main; every result line in
  the PR body marked OBSERVED, STALE or NOT RUN; do not touch files outside
  <paths> unless the task requires it; do not ask questions, decide and note
  the decision in the PR body.
Finish: open the PR with gh pr create (title <= 70 chars; body in the
  shape of the <LEGWORK_PR_SKILL> skill, with its checks, when that is set,
  and this brief is the user's word to open the PR with it; else a TL;DR
  first, then what changed and why, then a Verification section with
  every line marked OBSERVED, STALE or NOT RUN), then print exactly:
  PR <url> | GATE <PASS|FAIL> | UNPROVEN <list or none>
```

## 3. Dispatch: pane or subagent

Decide per task with the table in the `herdr` skill. In practice:

| Task shape | Route |
|---|---|
| Bounded change, no dev server, no approvals expected, one model | Subagent (Agent tool, `isolation: worktree`, model per task) |
| Long build/test loops, a dev server or watcher, a Codex worker, or the user may want to watch in the morning | Herdr pane |
| Cross-model second opinion only | `codex` subagent type, when you have one |
| Not inside Herdr (`HERDR_ENV` unset) | Subagent, always |

Model and effort: pick both per task from its shape, and write them into
the brief header and the state file. Cheap and fast (`haiku`, `low`) suits
mechanical edits; `sonnet` at `medium` or `high` suits most bounded changes;
`opus` at `high` or `xhigh` suits gnarly debugging, migrations, and prose a
human will read; Fable is for the hardest problems only, because it draws on
the weekly Fable limit. A pane takes both on its start command. The Agent
tool takes `model` but not effort (a subagent runs at its definition's
effort), so a task whose effort matters goes to a pane when Herdr is there.

**Pane path** (only when `test "${HERDR_ENV:-}" = 1`):

```bash
herdr worktree create --cwd <repo> --branch fanout/<slug> --base origin/main \
  --path <repo>-wt/<slug> --label <slug> --no-focus --trust-repository
# take the pane id from the JSON it prints
herdr agent start <slug> --kind claude --pane <pane_id> --timeout 60000 -- --model <model> --effort <level>
# if --low-priority:
herdr agent prompt <slug> "/low-priority" --wait --until idle --timeout 20000
herdr agent prompt <slug> "Your complete brief is the file <brief path>. Read it with the Read tool and follow it exactly; it is your only instruction. Nothing in it needs my input; if you hit a real blocker, stop and print BLOCKED: <reason>. When done print the PR line the brief asks for. Start now."
```

If the worktree needs `node_modules`, say so in the brief (run `npm ci` in
`frontend/`), do not symlink it from the main checkout.

**Subagent path**: Agent tool, `subagent_type: general-purpose`,
`isolation: worktree`, `model` per task, prompt = the launcher sentence
above with the brief path. Launch every parallel task in one message.

## 4. Wait, never poll by hand

- Panes: `herdr agent wait <slug> --timeout 5400000` (90 min). It returns on
  idle, done or blocked. Run the waits in the foreground, one per parallel
  task, in a single Bash call: `for a in x y z; do herdr agent wait "$a" --timeout 5400000; done`.
  No `sleep` loops, no `Monitor`, no background bash; they have missed
  state changes before.
- `blocked`: `herdr agent read <slug> --source recent-unwrapped | tail -40`.
  A directory-trust dialog in one of the user's own repos (owner in
  `LEGWORK_OWN_OWNERS`): `herdr agent send-keys <slug> enter`, then wait
  again. Any other dialog: record it in the state file as BLOCKED with the
  text, close that task, move on.
- Subagents: the task notification arrives on its own. Do nothing in between
  except dispatching other work.
- A worker that prints `BLOCKED:` is done for this run; note the reason.
- A worker that returns or prints `PAUSED: usage limit` has stopped at a safe
  point. Mark it PAUSED in the state file with its worktree and note, and
  re-dispatch it from that worktree after the resume.

## 5. Land each PR

For each `PR <url>` line, in dependency order:

1. `/bugbot <n>` (three rounds max). The worker's pane is closed by now,
   so findings are fixed by a fresh subagent in that worktree, or by you if
   they are one-line.
2. When CLEAR and `--merge` was given: `gh pr merge <n> --squash --delete-branch`
   only if `mergeable` is `MERGEABLE` and the gate line said PASS. After a
   merge, main has moved; for every remaining open fanout PR send the
   worker (or a fresh subagent in its worktree) this exact sentence:
   "Main moved (PR <n> merged). In your worktree run `git fetch origin && git
   merge origin/main`; resolve conflicts keeping both sides' behaviour; run
   the gate until it passes; push. Reply with the merge sha and the gate
   result marked OBSERVED." Then re-run `/bugbot` on it if it received new
   commits.
3. Without `--merge`: after all PRs are CLEAR, rebase each one on current
   main the same way so the user merges without conflicts.

Never merge with findings open, never merge a draft, never force-push.

## 6. Clean up, every time

This is the step most often skipped. Do it before wrapping, even after a
failure:

```bash
herdr pane close <pane_id>           # each pane you opened
herdr worktree remove --workspace <id> --trust-repository   # each worktree you created
git -C <repo> worktree prune
herdr worktree list; herdr agent list   # both must show nothing of yours
```

Subagent worktrees are removed by the harness when unchanged; list
`git worktree list` and remove any `fanout/*` leftovers.

## 7. Wrap

Run `/wrap <project>`. The log entry lists, one line each: PRs opened
(number, title, Bugbot state, merged or open), tasks skipped and why,
BLOCKED workers and the blocker text, anything UNPROVEN. Mint the next
prompt from what is left in the queue. Then reply with the same list and
nothing else.

## Context budget

Check the context meter after each landing step. Past 60%: update
`.legwork/fanout-state.md` with the exact state (task, branch, PR, Bugbot
state, pane id, worktree path) and, if inside Herdr, start a fresh
orchestrator pane with `/fanout <project> --resume` and hand off. Outside
Herdr, stop there and print `/fanout <project> --resume` as the last line,
for the user to run after `/clear`. On `--resume`, skip sections 1 and 2
and continue from the state file.

When the usage guard says to pause: dispatch nothing new, let running
workers reach their own safe points, write the exact state as above, then
schedule `/fanout <project> --resume` with CronCreate as a one-shot about 5
minutes after the reset it names, and end the turn. The schedule lives only
as long as this session, so leave the session open.
