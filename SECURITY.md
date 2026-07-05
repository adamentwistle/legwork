# Security

legwork runs Claude Code without a human watching. This file describes what
an autonomous session is allowed to do, how autonomy is granted, where
credentials live, and the guardrails and stop switches that keep a runaway
session bounded. The runner is `suite/legwork_runner.py`; the claims here
match it.

## What the runner can do

The runner fires each eligible project as one headless Claude Code session.
Every session launches the same way:

```
claude -p "<prompt>" \
  --output-format stream-json --verbose \
  --permission-mode acceptEdits \
  --add-dir <LEGWORK_DIR> \
  --allowedTools \
    "Bash(git:*)" "Bash(mkdir:*)" \
    "Bash(python3 core/build_dashboard.py:*)" \
    "Bash(python3 <LEGWORK_DIR>/core/build_dashboard.py:*)"
```

`--permission-mode acceptEdits` means file edits auto-accept in the target
repo (the session's working directory) and in the legwork repo (added with
`--add-dir`). On top of that, the allowlist grants exactly three things:
`git`, `mkdir`, and the dashboard rebuild. That is enough to read, write,
commit, and run `/wrap`.

Everything else is denied by name. Test runners, builds, package installs,
deploys: none of those run unless the target repo grants them in its own
`.claude/settings.json` allow rules. Per-repo allowlists are the consent
model. The runner never widens them and bypasses no permission checks.

Be honest about what that boundary is, though. `Bash(git:*)` is not a
narrow grant: git can be made to run arbitrary shell (a `git config
alias.x '!<command>'` followed by `git x`, a `git -c core.pager=<command>
log`, a repo-local `core.hooksPath`), so a session that wants a shell can
get one through git without tripping a permission prompt, and none of that
shows up in the commit-based audit below. The allowlist keeps an honest
session on the rails and stops accidental tool sprawl; it is not a sandbox
against a session that actively tries to escape. A session that hits a wall
is expected to say so and wrap honestly rather than work around it, and the
model's training carries real weight in that sentence.

Because `--add-dir <LEGWORK_DIR>` plus `acceptEdits` auto-accepts edits in
the legwork repo too, a session could in principle edit legwork's own
control-plane files. The runner raises the bar at the tool layer: it passes
a `--settings` file with deny rules on `core/**`, `suite/**` and
`scripts/**` under `LEGWORK_DIR`, the whole control plane: the hooks and
dashboard builder in `core/`, the runner, reviewer and n8n pipelines in
`suite/`, the installer in `scripts/`. Those deny rules cover the Edit,
Write and MultiEdit tools only, not Bash, so they block the direct edit
path but not a
git-mediated one: the same `Bash(git:*)` grant above could rewrite those
files through a checkout, an apply, or shell obtained via git. The post-hoc
audit below is the detection layer for exactly that, and it sees commits
only: a control-plane change left uncommitted in the working tree never
appears in it.

## Autonomy is opt-in

A project does not fire on its own. The runner only fires a project when a
human has opted it in. From `assess()` in the runner, an autonomous fire
requires `autonomy: loop` in the frontmatter and a `## Vision` section (the
standing brief that stands in for the human). Autonomy without a Vision is
refused.

`autonomy: loop` is set two ways, both human:

- The `/vision` interview, which writes the Vision and can opt the project
  into the loop.
- The Telegram `/loop` command, which refuses any project that has no Vision.

There is one exception, also human. A `fire_once` key in the frontmatter
stands in for the autonomy opt-in and the Vision section, for exactly one
session. It is the human hand-firing a single minted prompt from the phone
(the Telegram `/fire` command). The claim that flips `queued` to `running`
deletes the key, so the `fire_once` grant itself is single-use. Every other
eligibility guard still holds: a real prompt, a clean target repo, no
`blocked_on`, and under the daily cap.

One known limit: the opt-in gate governs how the runner chooses to fire,
not what a fired session may write. `projects/` is writable by every
session (that is how `/wrap` works) and the audit below deliberately
ignores it, so a session fired once could edit its own project file:
re-queue itself, set another `fire_once`, or grant itself `autonomy: loop`
plus a `## Vision`. Nothing alerts on that today. The mitigations are
indirect: the tracker is fully tracked in git, so self-granted autonomy is
a visible commit in `git log -- projects/`; the optional reviewer reads the
tracker entry after every session; and the daily fire and cost caps bound
how often anything fires regardless of who queued it. Treat the autonomy
gate as protection against drift and accident, not against a session
determined to keep itself running.

## Credentials

Never commit credentials.

- Webhook URLs (`LEGWORK_WEBHOOK_URL`, `LEGWORK_ALERT_URL`) live in the
  `config` file, which is gitignored, or in the environment. They are not in
  the repo. See `config.example`.
- The committed n8n workflow JSONs under `suite/reviewer/`,
  `suite/reply-capture/`, and `suite/alerts/` use `REPLACE_WITH_`
  placeholders for credential ids, the Telegram chat id, and the GitHub
  owner/repo. You fill those in on your own n8n instance; nothing real
  ships in the repo.
- The GitHub write-back token (used by the reply-capture pipeline to commit
  decisions and minted prompts through the Contents API) is a fine-grained,
  repo-scoped personal access token. It is held only as an n8n credential. It
  is never in the repo and never in a project file.

If you wire up the optional pipelines, the secrets stay in `config`, in your
environment, and in n8n. The public repo holds none of them.

## The Telegram surface

If you wire up reply-capture, the bot is the highest-privilege remote
surface in the system: its slash commands mint prompts (`/prompt`), queue
one-shot sessions (`/fire`), grant standing autonomy (`/loop`), and pause
or resume the runner, all through the GitHub write-back. Telegram bots are
publicly addressable, and the workflow as committed does not authenticate
the sender: the trigger fires on any message from any Telegram user, so
anyone who learns the bot's handle could drive your queue.

Do not activate it without an allowlist. Restrict the trigger to your own
Telegram user id (the message's `from.id`) before turning the workflow on;
suite/reply-capture/SETUP.md has the step. The same applies to any other
Telegram trigger you ever point at the bot.

## Guardrails

The session prompt itself carries a standing instruction to stop rather than
guess. From the runner's preamble: on any decision the brief does not cover,
or anything touching money, production deploys, credentials, sending things
to people, or deleting data, the session does not guess. It wraps with
status `escalated` and a `DECISION NEEDED` brief.

The optional reviewer applies the same always-escalate policy to finished
work. It escalates anything that touches:

- money or payments,
- anything deployed, sent, or public-facing,
- credentials or auth,
- destructive or hard-to-reverse operations,
- work that contradicts the project's stated intent.

The reviewer also always escalates self-modification of the pipeline.

The reviewer is best-effort triage, not a hard gate. Its evidence (the
tracker entry, the diff stat, the test output) is assembled from what the
session under review wrote, so a session can shape what the reviewer sees,
and a crafted wrap could coerce a pass. It reliably catches the ordinary
failures it exists for: honest-but-wrong work, unfinished tasks, accidental
scope. Do not model it as a security boundary against a session that lies.

The runner audits its own control plane. After every fire,
`audit_session_window()` diffs the legwork repo over the session window
(`claim_head..HEAD`). Any commit that touched the legwork repo outside
`projects/` and `dashboard/` raises a Telegram alert. The legwork repo is
the control plane: a worker session quietly editing the runner, the hooks, or
the reviewer is exactly the failure this catches. Human commits in the same
window can trip it too, and the alert says to ignore those if they are yours.

## Safety valves

Four independent limits bound a runaway loop:

- **Pause flag.** `touch .runner-pause` in the legwork repo and the runner
  fires nothing on the next tick; delete it to resume. A tracked twin,
  `.runner-pause-remote`, does the same and exists so the Telegram `/pause`
  and `/resume` commands can commit and delete it through the Contents API.
  It is re-checked right after the pull, so a fresh pause lands on the very
  next tick.
- **`LEGWORK_DAILY_CAP`.** Autonomous fires per project per calendar day,
  default 8. Counted from `runner.log`, so truncating or rotating that file
  resets the day's count. Once a project hits the cap it stops firing until the
  next day, even on retries.
- **`LEGWORK_DAILY_COST_CAP`.** An additional spend bound in dollars across all
  projects per calendar day; unset or `0` means no cap. Summed from the per-fire
  costs on `runner.log` completed lines, so truncating or rotating that file
  resets the day's count too. Once today's total reaches the cap the runner
  stops firing for the day and alerts once.
- **Per-session timeout.** `SESSION_TIMEOUT` (3600 seconds) terminates a
  stuck session: SIGTERM, then SIGKILL after a grace period.

## Reporting

This is a personal project, shipped as-is. There is no formal SLA and no
guaranteed response time. If you find a security issue, open a GitHub issue.
For something sensitive that should not be public, use a private contact
instead of filing it in the open.
