# Architecture

legwork is an autonomous project queue for Claude Code. It implements the
legwork queue: a repo of markdown project files, a runner that fires queued
prompts as headless Claude Code sessions, and an optional review pipeline that
triages every session and escalates to a human only when a human decision is
genuinely required.

This document describes how the pieces fit together. Every claim here is
grounded in the files under this repo. The canonical shape is the diagram in
README.md; this document expands the prose around it and does not contradict
it.

## Data flow

```
projects/*.md            source of truth: one markdown file per project
     |                   (frontmatter, optional Vision, Next prompt, Log)
     v
build_dashboard.py  -->  dashboard/index.html   (static build artifact, stdlib)

legwork_runner.py       launchd/cron, every ~5 min:
     |                   pulls, then fires every queued project that has
     |                   autonomy: loop and a ## Vision section, one headless
     |                   "claude -p" session each, in parallel
     v
headless Claude Code session   (acceptEdits + git/mkdir/dashboard only)
     |   SessionStart hook: record the repo HEAD for this session
     |   SessionEnd hook:   POST session-scoped git evidence + tracker entry
     v
LEGWORK_WEBHOOK_URL  ....................   optional review pipeline (n8n)
     |
     v
reviewer (LLM)  -->  pass | revise | escalate  -->  Telegram letter
     ^                                                   |
     |   you reply in Telegram                           v
reply-capture  <--  commit decision, mint/keep next prompt, status -> queued

(no webhook set?  the runner still fires and wraps; the pipeline is skipped)
```

The source of truth is `projects/*.md`: one markdown file per project. Each
file holds frontmatter (status, repo, autonomy and so on), an optional
`## Vision` section that is the standing brief, a `## Next prompt` fenced
block, and an append-only `## Log`. Everything else is derived from these
files or acts on them.

`scripts/build_dashboard.py` reads every project file and regenerates
`dashboard/index.html` wholesale. The html is a build artifact. Nothing is
hand-edited there; you change a project file and rebuild.

`scripts/legwork_runner.py` runs every five minutes under launchd or cron. On
each tick it pulls the legwork repo, then assesses every project file. It
fires every eligible project at once, one headless `claude -p` session each.
Eligible means status `queued`, `autonomy: loop` in the frontmatter, a
`## Vision` section, a clean target git tree, a real Next prompt, and fewer
than `LEGWORK_DAILY_CAP` fires today. Before launching a session it claims the
project by flipping its status from `queued` to `running` on the remote, so a
later tick will skip it while the session is in flight.

Each headless session runs with `--permission-mode acceptEdits` and a narrow
tool allowlist (git, mkdir, the dashboard rebuild). It reads the project file,
does the work in the target repo, commits, and wraps the tracker.

Two Claude Code hooks bracket the session.
`scripts/session_start_hook.sh` records the target repo's HEAD keyed by session
id, so the end of the session can report only what this session changed.
`scripts/session_end_hook.sh` gathers session-scoped git evidence (the diff and
commits since the recorded HEAD) plus the project's tracker entry, and POSTs it
to `LEGWORK_WEBHOOK_URL`. When `LEGWORK_WEBHOOK_URL` is unset the end hook
logs a skip and does nothing else.

When the webhook is set, the optional review pipeline takes over. The reviewer
(an LLM call in an n8n workflow) reads the evidence against the project's stated
intent and returns one of three verdicts: `pass`, `revise`, or `escalate`. A
`pass` or `revise` can move the project forward without you. An `escalate`
becomes a Telegram letter: a short decision brief you can answer with a single
letter. You reply in Telegram, the reply-capture pipeline records your decision,
mints or keeps the next prompt, and flips the status back to `queued`. The
runner picks it up on its next tick. You stay in the loop by exception, not by
babysitting.

With neither `LEGWORK_WEBHOOK_URL` nor `LEGWORK_ALERT_URL` set, the runner
still fires sessions and they still wrap. The review post and the Telegram
alerts are simply skipped. You can run the queue, runner and dashboard with no
n8n at all.

## Components

### Projects (`projects/*.md`)

The source of truth. One markdown file per project. The format is defined in
`.claude/skills/legwork-tracker/SKILL.md`:

- Frontmatter with one-line values: `name`, `category`, `status`, `energy`,
  `description`, `repo`, `updated`, and the optional keys `autonomy`,
  `account`, `blocked_on`.
- An optional `## Vision` section: the standing brief, written as North star,
  Done means, Guardrails, Escalate when, Taste. Replaced wholesale when
  re-run, never appended to.
- A `## Next prompt` section with one fenced code block: the cold-start prompt
  the next session executes.
- An append-only `## Log` of dated bullets, newest first.

Statuses are a fixed set: `queued`, `running`, `review`, `escalated`, `done`,
`icebox`. legwork gitignores `/projects/` so you keep your own projects in a
private repo; `examples/projects/` holds fake samples.

### Dashboard builder (`scripts/build_dashboard.py`)

Stdlib only. It reads simple frontmatter, the first fenced block under
`## Next prompt`, and the bullets under `## Log` from every project file, then
writes `dashboard/index.html`. Every run replaces the file wholesale: all
styling lives in the `TEMPLATE` constant, and project data never lives in the
script. The build groups projects by status, surfaces a "Needs you" section for
escalated projects, computes a freshness pill from the newest of the
frontmatter date and the latest log entry, and renders a changelog of every
dated log line. The html is a build artifact and is gitignored.

### Runner (`scripts/legwork_runner.py`)

The runner. Run by launchd or cron every five minutes, or by hand:

```
python3 scripts/legwork_runner.py            one tick
python3 scripts/legwork_runner.py --dry-run  show eligibility, change nothing
python3 scripts/legwork_runner.py --doctor   preflight checklist, change nothing
```

Zero dependencies. It reads config at startup via `load_config()`, which loads
KEY=VALUE lines from a `config` file (or `$LEGWORK_CONFIG`) into the
environment, with real environment variables winning. See "The runner" below
for eligibility, parallelism, the permission model, and failure handling.

### Hooks (`scripts/session_start_hook.sh`, `scripts/session_end_hook.sh`)

Claude Code hooks that bracket every session.

The SessionStart hook records the target repo's HEAD at session open, keyed by
session id, in `.session-heads/`, along with the repo path. It prunes markers
older than three days. The recorded path matters because an autonomous session
ends with its working directory in the legwork repo (it cd'd there to wrap),
so the end hook cannot trust its own cwd to find the target repo.

The SessionEnd hook reads that marker, diffs the target repo from the recorded
HEAD, and assembles a payload: repo (resolved to the project file stem),
branch, last commit, diff stat, session commits, uncommitted file names, the
project's tracker entry, and optional test output from
`<repo>/.legwork/last_test_output.txt`. It POSTs the payload to
`LEGWORK_WEBHOOK_URL`. It is a no-op that logs a skip when
`LEGWORK_WEBHOOK_URL` is unset, and skips sessions that ended via clear or
resume. Every invocation is logged to `hook.log`. If a session ends without
the hook firing (for example an account whose config carries no hooks), the
runner posts the review request itself, so the loop closes either way.

### launchd agent (`scripts/com.legwork.runner.plist`)

A launchd agent template with `__LEGWORK_DIR__` and `__PYTHON__` placeholders.
`StartInterval` defaults to 300 seconds (every five minutes). Substitute the
placeholders, drop it in `~/Library/LaunchAgents/`, and load it.

### Reviewer pipeline (`reviewer/`)

The optional review pipeline as an importable n8n workflow.

- `n8n-review-workflow.json`: the importable workflow.
- `n8n-build-node.js`: the build node that assembles the review request. It is
  the source of truth for the rubric. The reviewer model comes from
  `REVIEWER_MODEL`, default `claude-sonnet-4-6`.
- `rubric.md`: a readable mirror of the rubric in the build node.

The reviewer judges session evidence against the project's stated intent (its
Task and Done when lines) and returns JSON with one verdict: `pass`, `revise`
(with a fix prompt), or `escalate` (with a decision brief answerable in one
letter). The rubric is opinionated about what always escalates regardless of
confidence: anything touching money, anything deployed or sent or
public-facing, credentials or auth, destructive or hard-to-reverse operations,
evidence that contradicts the stated task, and self-modification of the
pipeline.

### Reply-capture pipeline (`reply-capture/`)

The optional Telegram pipeline.

- `n8n-reply-capture-workflow.json`: the importable workflow.
- `SETUP.md`: setup and the full command reference.

Reply to a review letter, or send slash commands, to drive the queue from your
phone. A `NEEDS YOU` reply with an option letter logs the decision, mints a
fresh prompt that carries it out, and queues the project. A `PASS continue` or
`REVISE continue` moves the project forward. Slash commands (`/board`, `/show`,
`/fire`, `/loop`, `/prompt`, `/pause`, `/resume`) drive the whole queue. Every
read and write goes through the GitHub Contents API; nothing touches the
machine directly, and the runner picks changes up on its next tick. The
write-back token is a fine-grained, repo-scoped PAT held only as an n8n
credential, never in the repo.

### Alerts (`alerts/`)

The optional runner-alerts n8n workflow (`n8n-alerts-workflow.json`). It
receives plain text on `LEGWORK_ALERT_URL` and forwards it to Telegram. The
runner posts a stall alert when ticking has been blocked longer than
`STALL_ALERT_AFTER`, a daily heartbeat after `HEARTBEAT_HOUR` (last fire,
eligibility per autonomy project, stale running projects, escalated count), and
an audit alert when a session window touches the legwork repo outside
`projects/` or `dashboard/`. With `LEGWORK_ALERT_URL` unset, all of these are
quietly skipped.

### Skill and commands (`.claude/`)

The legwork-tracker skill (`.claude/skills/legwork-tracker/SKILL.md`) defines
the project file format, the status set, the Vision shape, the prompt-minting
rules, and the wrap procedure. It is what mints and updates project files at
the end of every session. The commands under `.claude/commands/` are the
verbs: `/add` (start a project), `/log` (update without a work session),
`/pickup` (reload context), `/shelve` (icebox), `/vision` (capture the standing
brief and optionally grant autonomy), and `/wrap` (close out a session and mint
the next prompt). `/vision` is the single gate into autonomy: it captures the
Vision and is the only place `autonomy: loop` is set.

### Tests (`tests/test_legwork.py`)

A stdlib test suite, 69 tests, covering the runner, the dashboard builder and
the hooks. The hook tests run the real shell scripts as subprocesses against
throwaway git repos; nothing touches the real legwork repo, the webhook, or
the network. Run:

```
python3 -m unittest discover -s tests
```

## The runner

### Eligibility

On each tick the runner reads the legwork repo, pulls, and then assesses every
project file. A project is eligible to fire only when all of these hold:

- `status: queued`.
- `autonomy: loop` in the frontmatter. This is the explicit human opt-in, set
  via `/vision`.
- A `## Vision` section. Autonomy without a Vision is refused: the Vision is
  the standing brief that stands in for the human.
- The `repo` points at an existing git repository with a clean working tree.
- The Next prompt is a real prompt, not a `Human action`, `DECISION NEEDED`, or
  `PROMPT NEEDED` marker.
- Fewer than `LEGWORK_DAILY_CAP` fires for this project today (counted from
  `runner.log`).
- No `blocked_on` key in the frontmatter.

There is one exception to the autonomy and Vision gate. A `fire_once` key,
which only a human can set (via the Telegram `/fire` command), stands in for
both for exactly one session. It is the human hand-firing the minted prompt;
the claim that flips `queued` to `running` consumes the key, so the project
cannot fire again without the human. Every other guard still holds.

`--dry-run` prints the eligibility verdict for every project and changes
nothing.

### Parallel firing and the legwork-repo write lock

One tick fires every eligible project at once, one session in flight per
project. Target-repo sessions run fully parallel in worker threads. The
legwork repo is shared state, so every write section (claim, repair, dashboard
rebuild, audit window) takes an in-process write lock and pushes before
releasing it. The lock only serialises the runner's own worker threads against
each other, so concurrent fires never race the index or sweep each other's
commits. It does not reach cross-process writers: a session's own `/wrap` or an
n8n remote commit lands independently, and `push_with_rebase`'s rebase-and-retry
reconciles those by pulling and replaying on conflict, not the lock.

Two projects that point at the same target repo never fire in the same window,
because sessions sharing one working tree would collide. The oldest claim wins
and the other waits for a later tick. A lock file (`.runner.lock`) makes
overlapping ticks exit quietly; a stale lock from a dead run is reclaimed.

### Permission model

Sessions run headless with `--permission-mode acceptEdits`. File edits
auto-accept in the target repo and the legwork repo, plus a narrow allowlist
of git, mkdir, and the dashboard rebuild, which is just enough to work, commit
and wrap. Everything else (test runners, builds, deploys) is denied unless the
target repo's own `.claude/settings.json` allow rules grant it. The per-repo
allowlist is the consent mechanism, and the runner never widens it. No
permission checks are bypassed.

Because `--add-dir <LEGWORK_DIR>` lets a session edit the legwork repo, the
runner also passes a `--settings` file with Edit/Write deny rules on the
control plane under `LEGWORK_DIR` (`scripts/**`, `reviewer/**`,
`reply-capture/**`, `alerts/**`, and the hook scripts). This blocks a worker
session from rewriting the runner, hooks, or reviewer at the tool layer,
independent of any webhook. `audit_session_window()` remains a second,
post-hoc layer over the same control plane (see below).

### Crashes (repair status)

A claimed project reads `status: running`. If a session exits while still
running, the runner repairs the status instead of leaving it stale. It tells a
real crash from a session that did wrap but forgot the status flip: if the
session window holds a non-runner commit that touched the project's tracker
file, the project moves to `review` as a wrapped session; otherwise it is
flagged as exited without wrapping and moved to `review`, and the reviewer
webhook is told. Either way the runner appends a log line pointing at the
transcript in `.runner-logs/`.

### Transient API failures (backoff)

A session that died on a transient cloud error (529 overloaded, rate limit,
5xx, connection faults) with zero turns and zero cost is treated as nothing
attempted. The runner re-queues it quietly so a later tick retries, still
bounded by `LEGWORK_DAILY_CAP`, and the reviewer has nothing to look at. The
re-queued project then waits out an escalating backoff before it fires again:
`TRANSIENT_BASE` (15 minutes), doubling per consecutive transient crash up to
`TRANSIENT_CAP` (2 hours), so a cloud outage is not hammered every five
minutes. A clean fire clears the count. A failure that did real work before
crashing is a genuine failure and goes to review, not a quiet retry.

### Usage limits (defer the account)

A session killed by a usage limit is the account's problem, not the project's.
The runner defers the whole account until the named reset (parsed from the
error text), or `USAGE_BLOCK_DEFAULT` (30 minutes) when no reset clock is
given, so sibling projects on the same account do not fire straight into the
same wall. The backoff and usage state live in `.runner-state.json`.

### Other behaviours

The runner reads config at startup via `load_config()`. A `.runner-pause` flag
(or its tracked twin `.runner-pause-remote`, which the Telegram `/pause` and
`/resume` commands commit and delete) stops all firing. The per-session timeout
(`SESSION_TIMEOUT`) terminates a stuck session. An optional spend guard,
`LEGWORK_DAILY_COST_CAP` (dollars; unset or `0` means no cap), sums today's
per-fire costs from `runner.log` and stops firing for the day once the cap is
reached, alerting once like the stall alert. After every fire the runner audits
the session window: any commit that touched the legwork repo outside
`projects/` or `dashboard/` raises an alert, because the legwork repo is the
control plane. `audit_session_window()` always writes an `AUDIT` line to
`runner.log`, even with no alert webhook set. A minted prompt may carry
`Model:` and `Effort:` lines, which the runner strips and passes through as
`--model` and `--effort`. `--doctor` runs a preflight checklist (and, like
`--dry-run`, surfaces frontmatter validation warnings) without changing
anything; the validation is warnings only and never blocks a fire.

## Invariants

These hold across the system. Breaking one breaks an assumption the rest of the
pieces rely on.

- `dashboard/index.html` is a build artifact. Never hand-edit it. Change a
  project file and regenerate with `python3 scripts/build_dashboard.py`. The
  file is gitignored.
- `build_dashboard.py` stays stdlib-only. No third-party dependencies.
- Project logs are append-only. Prepend a dated bullet; never rewrite or delete
  old entries.
- The status set is fixed: `queued`, `running`, `review`, `escalated`, `done`,
  `icebox`. Nothing else is valid.
- Autonomy is opt-in per project, and only a human grants it. `autonomy: loop`
  is set via the `/vision` interview or the Telegram `/loop` command, which
  refuses any project that has no Vision. `fire_once` is one-shot human consent,
  consumed on claim.
- The per-repo allowlist is the consent model. Sessions get edits plus git,
  mkdir and the dashboard rebuild by default; anything more is granted by the
  human in that repo's own `.claude/settings.json`, and the runner never widens
  it.
- The review pipeline is optional. With neither `LEGWORK_WEBHOOK_URL` nor
  `LEGWORK_ALERT_URL` set, the runner still fires sessions and they still
  wrap; the review post and the Telegram alerts are skipped.
- The runner audits its own control plane. Any commit in a session window that
  touches the legwork repo outside `projects/` and `dashboard/` raises an
  alert.
- Self-modification of the control plane is blocked locally at the tool layer.
  The runner passes `--settings` deny rules on `scripts/**`, `reviewer/**`,
  `reply-capture/**`, `alerts/**`, and the hook scripts under `LEGWORK_DIR`, so
  a worker session cannot rewrite the runner, hooks, or reviewer regardless of
  any webhook, and `audit_session_window()` records any control-plane touch in
  `runner.log` after the fact. When the optional reviewer is wired, it
  additionally escalates self-modification: a diff of the legwork repo that
  touches its hooks, reviewer rubric, n8n workflow, or dashboard build script
  is escalate regardless of confidence.

## Design choices

### One markdown file per project

The project file is the source of truth, not a database row or a config blob. A
human can read it, edit it, and grep it. A headless session can read it to
recover full context from a cold start. The dashboard, the runner, the hooks
and the n8n pipelines all key on the same plain file. There is no separate
store to keep in sync, and git history is the audit trail.

### Headless sessions

Each queued prompt fires as a real headless Claude Code session
(`claude -p`) in the target repo, with the same tooling an interactive session
has. The runner does not reimplement an agent; it schedules and brackets
sessions and gets out of the way. The minted prompt is written to be executable
from a cold start, so the same prompt works whether a human copies it from the
dashboard or the runner fires it.

### Reviewer by exception

The novel idea is that an LLM reviewer triages every autonomous session and
escalates to a human only when a human decision is genuinely required: money,
deploys, credentials, sending things to people, deleting data, or work that
contradicts the stated intent. Most sessions pass or get a fresh fix prompt
without involving you. You are pulled in by exception, through a Telegram letter
you can answer with one letter, instead of babysitting every run.

### Stdlib-only, no dependencies

The runner, the dashboard builder and the test suite use the Python standard
library only. There is nothing to install and nothing to keep up to date.
launchd, cron and manual runs share one `config` file as their source of truth.
The optional pieces (reviewer, reply-capture, alerts) live in n8n and are wired
in through webhook URLs; the core runs without any of them.
