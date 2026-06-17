# Security

legwork runs Claude Code without a human watching. This file describes what
an autonomous session is allowed to do, how autonomy is granted, where
credentials live, and the guardrails and stop switches that keep a runaway
session bounded. The runner is `scripts/legwork_runner.py`; the claims here
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
    "Bash(python3 scripts/build_dashboard.py:*)" \
    "Bash(python3 <LEGWORK_DIR>/scripts/build_dashboard.py:*)"
```

`--permission-mode acceptEdits` means file edits auto-accept in the target
repo (the session's working directory) and in the legwork repo (added with
`--add-dir`). On top of that, the allowlist grants exactly three things:
`git`, `mkdir`, and the dashboard rebuild. That is enough to read, write,
commit, and run `/wrap`.

Everything else is denied. Test runners, builds, package installs, deploys,
arbitrary shell: none of it runs unless the target repo grants it in its own
`.claude/settings.json` allow rules. Per-repo allowlists are the consent
model. The runner never widens them and bypasses no permission checks. A
session that hits a wall is expected to say so and wrap honestly rather than
work around it.

Because `--add-dir <LEGWORK_DIR>` plus `acceptEdits` auto-accepts edits in the
legwork repo too, a session could in principle edit legwork's own control-plane
files. The runner mitigates this at the tool layer: it passes a `--settings`
file with Edit/Write deny rules on `scripts/**`, `reviewer/**`,
`reply-capture/**`, `alerts/**`, and the hook scripts under `LEGWORK_DIR`, so a
worker session cannot rewrite the runner, hooks, or reviewer. The post-hoc audit
below is a second layer over the same files.

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
deletes the key, so a `fire_once` project cannot fire again without the human
setting it once more. Every other eligibility guard still holds: a real
prompt, a clean target repo, no `blocked_on`, and under the daily cap.

## Credentials

Never commit credentials.

- Webhook URLs (`LEGWORK_WEBHOOK_URL`, `LEGWORK_ALERT_URL`) live in the
  `config` file, which is gitignored, or in the environment. They are not in
  the repo. See `config.example`.
- The committed n8n workflow JSONs under `reviewer/`, `reply-capture/`, and
  `alerts/` use `REPLACE_WITH_` placeholders for credential ids, the Telegram
  chat id, and the GitHub owner/repo. You fill those in on your own n8n
  instance; nothing real ships in the repo.
- The GitHub write-back token (used by the reply-capture pipeline to commit
  decisions and minted prompts through the Contents API) is a fine-grained,
  repo-scoped personal access token. It is held only as an n8n credential. It
  is never in the repo and never in a project file.

If you wire up the optional pipelines, the secrets stay in `config`, in your
environment, and in n8n. The public repo holds none of them.

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
