# Configuration

legwork keeps its configuration in one place so launchd, cron and manual runs
all agree. The legwork runner reads it; a few values are read by other parts
of the system and are documented here too, so you only have to look in one
file.

## How config is loaded

`scripts/legwork_runner.py` calls `load_config()` at startup, before it reads
any setting. `load_config()` looks for a config file in this order:

1. the path in the `LEGWORK_CONFIG` environment variable, if it is set
2. otherwise a file named `config` beside the repo root (the same directory
   `config.example` lives in)

The first file that exists is used; a missing file is fine, the runner just
falls back to defaults and the environment.

Rules for the file:

- Lines are `KEY=VALUE`.
- A line that is blank, or starts with `#`, or has no `=`, is ignored. `#`
  starts a comment.
- `$HOME`, other `$VARS` and a leading `~` are expanded, so the file stays
  machine-agnostic.
- Surrounding single or double quotes around a value are stripped.
- Real environment variables always win. The file uses `setdefault`, so a
  value already present in the environment is never overwritten by the file.

`config.example` is the committed template. Copy it to `config` and edit:

```
cp config.example config
```

`config` is gitignored, so your real paths and webhook URLs never get
committed. This matters most for launchd, which does not read your shell
profile: the config file is how the runner gets its settings under launchd.

## Variables

These are read by the legwork runner (`scripts/legwork_runner.py`), from the
environment or the config file.

| Variable | Default | Required | What it does |
| --- | --- | --- | --- |
| `LEGWORK_DIR` | `$HOME/legwork` | optional | The legwork repo: where `projects/*.md`, `runner.log`, the dashboard and runner state live. Everything the runner reads and writes is under here. |
| `LEGWORK_DAILY_CAP` | `8` | optional | Autonomous fires per project per calendar day. Counted from `runner.log`, so truncating or rotating that file resets the day's count. When a project hits the cap it stops firing until the next day. |
| `LEGWORK_DAILY_COST_CAP` | `0` | optional | Spend guard in dollars across all projects per calendar day. Unset or `0` means no cap. Summed from the per-fire costs on `runner.log` completed lines; once today's total reaches the cap the runner stops firing for the day and alerts once. Like `LEGWORK_DAILY_CAP`, truncating or rotating `runner.log` resets the day's count. |
| `LEGWORK_WEBHOOK_URL` | unset | optional | The n8n webhook that receives session evidence for review. The SessionEnd hook posts to it; the runner posts to it directly if a session's hook did not fire. Unset means the review post is skipped. |
| `LEGWORK_ALERT_URL` | unset | optional | The n8n webhook that receives runner stall alerts and the daily heartbeat. Unset means those alerts are skipped. |
| `LEGWORK_LOCAL_REVIEW` | unset | optional | The no-n8n alternative to `LEGWORK_WEBHOOK_URL`. Truthy (`1`/`true`/`yes`/`on`) makes the runner triage each finished session in-process with a `claude -p` call and write the verdict back to the project file. Only takes effect when `LEGWORK_WEBHOOK_URL` is unset (the webhook wins if both are set). See `scripts/legwork_review.py`. |
| `REVIEWER_MODEL` | `claude-sonnet-4-6` | optional | The model the reviewer call uses. Read by the runner for the local reviewer, and by `reviewer/n8n-build-node.js` for the n8n pipeline. A full model id or a short alias. |
| `CLAUDE_CONFIG_DIR` | unset | optional | The Claude config dir autonomous sessions run under, so a run never inherits whatever account your interactive shell defaults to. Unset means inherit the default config. |
| `CLAUDE_CONFIG_DIR_<ACCOUNT>` | unset | optional | Per-account override. A project frontmatter `account: <name>` maps to `CLAUDE_CONFIG_DIR_<NAME>` (the name uppercased), giving that account its own config dir. |
| `LEGWORK_CONFIG` | unset | optional | Explicit path to the config file. When set, the runner reads this file instead of the default `config` beside the repo root. |

Notes:

- `LEGWORK_DIR` accepts `$HOME` and `~`; both are expanded.
- For `CLAUDE_CONFIG_DIR_<ACCOUNT>`, the suffix is the account name uppercased.
  A project with `account: work` reads `CLAUDE_CONFIG_DIR_WORK`. If that
  variable is unset, the session falls back to `CLAUDE_CONFIG_DIR`, and if that
  is also unset, to the default config.

## Applied outside the runner

These settings are not read by `scripts/legwork_runner.py`. They live next to
the code that uses them. They are documented here, and in `config.example`, so
config stays in one mental place, but you set them where they are read.

- `REVIEWER_MODEL` (above) is also read by `reviewer/n8n-build-node.js`, the
  build node for the n8n review pipeline. The `config` file sets it for the
  local reviewer (the runner reads it); for the n8n side, set it in the
  environment where that node runs, or edit the fallback in the build node.

- The launchd tick interval is the `StartInterval` key (in seconds) in
  `scripts/com.legwork.runner.plist`. Default `300` (every 5 minutes). Change
  it in the installed plist, then reload the launchd agent. If you run the
  runner from cron instead, the interval is your crontab schedule, not this
  key.

- `LEGWORK_LEVEL` and `LEGWORK_TICK_MINUTES` are written by `./install.sh`
  and only read back by it, to pre-fill the level question (1 is the manual
  loop, 2 is autonomy) and the tick-interval question on a re-run. The
  runner ignores both; the live tick cadence is whatever the launchd agent
  or crontab line was installed with (the bullet above).

## The optional pipeline

The review pipeline is optional. With neither `LEGWORK_WEBHOOK_URL` nor
`LEGWORK_ALERT_URL` set, the runner still fires eligible sessions and the
sessions still wrap; only the review post and the Telegram alerts are skipped.
You can run the queue, the runner and the dashboard with no n8n at all.

To get reviewer-by-exception without n8n, set `LEGWORK_LOCAL_REVIEW` instead of
the webhook. The runner then triages each finished session itself with a
`claude -p` call and writes the verdict back to the project file: a `pass`
requeues the wrapped prompt, a `revise` installs the reviewer's fix prompt and
requeues, and an `escalate` flips the project to `escalated` with a DECISION
NEEDED brief that surfaces on the dashboard's Needs-you zone. `LEGWORK_ALERT_URL`
remains optional on top of this; when it is set the verdict letter is also sent
to Telegram. See `scripts/legwork_review.py`.
