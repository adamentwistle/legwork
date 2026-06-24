# legwork

[![CI](https://github.com/adamentwistle/legwork/actions/workflows/ci.yml/badge.svg)](https://github.com/adamentwistle/legwork/actions/workflows/ci.yml)

legwork is an autonomous project queue for Claude Code. Each project is one markdown file. A runner fires queued prompts as headless Claude Code sessions; an optional LLM reviewer triages the output and escalates to a human only when a decision is actually needed.

The idea worth stealing is reviewer-by-exception. An LLM reviewer reads every autonomous session and only escalates to a human when a human decision is genuinely required: money, deploys, credentials, sending things to people, deleting data, or work that contradicts the stated intent. Everything else passes or is sent back for another pass. You stay in the loop by exception, not by babysitting.

## How it works

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

Each project is a markdown file with frontmatter, an optional `## Vision` brief, a `## Next prompt` fenced block, and an append-only `## Log`. The runner ticks every five minutes, pulls the legwork repo, and fires every eligible project as a headless `claude -p` session. Eligibility is strict: status `queued`, `autonomy: loop`, a `## Vision` section, a clean target git tree, a real next prompt, and under the daily cap. Sessions run with `--permission-mode acceptEdits` plus a narrow allowlist of git, mkdir, and the dashboard rebuild; anything more needs the target repo's own `.claude/settings.json`. When a review webhook is set, the optional n8n pipeline triages each session and writes the outcome back to the queue. Don't want to run n8n? Set `LEGWORK_LOCAL_REVIEW` and the runner triages each session itself with a `claude -p` call, writing the pass/revise/escalate verdict straight back to the project file: reviewer-by-exception with zero extra infrastructure. With neither, the runner still fires and wraps and review is simply skipped.

## Dashboard

The dashboard is a single static HTML file built from `projects/*.md` by `scripts/build_dashboard.py`, which uses the Python standard library only and rewrites `dashboard/index.html` wholesale on each run. It is a build artifact, gitignored, and read straight from disk in a browser. It shows each project as a card with its status, energy, description, blocker, and staleness, so the queue is visible at a glance with no server.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/dashboard-dark.png" />
  <img alt="The legwork dashboard: a queue ribbon, a needs-you zone, status-spined project cards, and a changelog timeline" src="docs/dashboard-light.png" />
</picture>

<sub>Light and dark both ship in <code>docs/</code>; the image above follows your GitHub theme.</sub>

## Quickstart

Requirements: `python3`, the Claude Code CLI, and `git`.

legwork is the legwork repo: the runner, the dashboard builder, the config file and your project files all live inside one checkout. Clone your own legwork repo — a fork, or any git remote you control — so the runner can commit your project files back to it; the local runner and dashboard also work with no remote, so a plain clone is enough just to try it.

```
git clone <your-legwork-remote> "$HOME/legwork"
cd "$HOME/legwork"
./install.sh
```

`./install.sh` is an interactive, dependency-free wizard. It asks for every value legwork can be configured with (the legwork dir, the daily fire and cost caps, the review mode and reviewer model, an optional dedicated Claude config dir, the tick interval), shows you the `config` it will write, then offers to activate the pieces that live outside the repo, asking before each one:

- write `config` and create `projects/` and `.runner-logs/`
- install and load the launchd agent (macOS) or a crontab line (Linux)
- register the SessionStart/SessionEnd hooks in your Claude `settings.json`

Then fill the queue: add projects with the `/add` skill and grant autonomy per project with `/vision`. Verify any time with `python3 scripts/legwork_runner.py --doctor`.

Prefer to do it by hand, or want the optional n8n review, reply-capture, and alerts pipelines? See [SETUP.md](SETUP.md); every step the wizard automates is also written out there.

## What this is not

- Not a hosted service, not an agent-ops platform, not a paid tier.
- Not multi-vendor or multi-model breadth. It is opinionated and Claude-Code-first.
- Not a maintenance commitment. Shipped as-is, issues welcome, no SLA, PRs optional.
- Not a rewrite or a gold-plated framework.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): how the pieces fit together.
- [SETUP.md](SETUP.md): install the runner and wire the optional pipelines.
- [CONFIG.md](CONFIG.md): every config variable.
- [SECURITY.md](SECURITY.md): the permission and escalation model.
- [CONTRIBUTING.md](CONTRIBUTING.md): scope, non-goals, and how to send a PR.
- [LICENSE](LICENSE): the license.

## Status

Shipped as-is. This is an opinionated proof of capability, not a product. Issues are welcome, but there is no SLA and no maintenance commitment.
