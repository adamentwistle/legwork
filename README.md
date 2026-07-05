# legwork

[![CI](https://github.com/adamentwistle/legwork/actions/workflows/ci.yml/badge.svg)](https://github.com/adamentwistle/legwork/actions/workflows/ci.yml)

legwork is a project queue for Claude Code that survives you walking away. Each project is one markdown file with a status, an append-only log, and a ready-to-run next prompt. You end a work session with `/wrap`, which records what happened and writes the prompt your next session should start from, while the context is still hot. Days later, `/pickup` briefs you back into the project in thirty seconds instead of twenty minutes of re-reading. A static dashboard shows the whole queue at a glance. And when a project has earned it, there is a level 2: a runner that fires those queued prompts as unattended Claude Code sessions, with an LLM reviewer that only interrupts you when a human decision is actually needed.

<img alt="The legwork dashboard: a queue ribbon, a needs-you zone, status-spined project cards, and a changelog timeline" src="docs/dashboard-light.png" />

<sub>The dashboard is one static HTML file, rebuilt from your project files by a standard-library Python script. No server, no dependencies. Light is the default; a blackboard <a href="docs/dashboard-dark.png">dark theme</a> is one toggle away.</sub>

## Install

The fastest way in — from inside Claude Code, add the marketplace and install the plugin:

```
/plugin marketplace add adamentwistle/legwork
/plugin install legwork@legwork
```

That gives you the six slash commands (`/add`, `/wrap`, `/pickup`, `/log`, `/shelve`, `/vision`) and the legwork-tracker skill in every repo on your machine, backed by a queue in `~/legwork` (set `LEGWORK_DIR` to move it). The plugin is this repo's [`core/`](core/) directory itself — one editable source, nothing copied. For the self-rebuilding dashboard and the optional level-2 runner, clone the repo and run the wizard instead — see [Quickstart](#quickstart).

## The loop

The core of legwork is a habit, not a daemon: never end a session without writing down what the next session should do.

```
/add      create projects/<name>.md: status, description, a real first prompt
 work     any Claude Code session, in any repo
/wrap     log what happened, write the next prompt while context is hot
 away     days pass; the dashboard shows every project and its next step
/pickup   a 30-second re-entry briefing; run the queued prompt or adjust it
```

A project file looks like this (six full samples, one per status, live in [`examples/projects/`](examples/projects/)):

````markdown
---
name: Garden Planner
status: queued
description: A small CLI that plans a vegetable bed by frost dates.
repo: ~/code/garden-planner
updated: 2026-05-28
---

## Next prompt

```text
Read README.md and the last three log entries first.

Task: add a --frost-dates flag that overrides the zone-derived dates.

Done when: the calendar shifts to the supplied dates and the tests pass.
```

## Log

- 2026-05-28: Zone table shipped. Next: frost-date override.
````

## Quickstart

Requirements: `python3` (3.9 or newer), the Claude Code CLI, and `git`.

```
git clone <your-legwork-remote> "$HOME/legwork"
cd "$HOME/legwork"
./install.sh
```

You supply the clone: fork this repo or push a copy to a private remote you control. Cloning this repo directly is fine just to try it; nothing in the manual loop needs a remote.

`./install.sh` is an interactive, dependency-free wizard. Its first question is which level you are installing:

- **Level 1, the manual loop** (the default): one question — where the repo lives — then it writes `config`, creates `projects/`, and offers two opt-ins: copying the slash commands (`/add`, `/wrap`, `/pickup`, `/vision`, `/log`, `/shelve`) and the legwork-tracker skill into user-level `~/.claude`, so the loop works from any repo on your machine, and registering the session hooks, which with no webhook set simply rebuild the dashboard after every session so the queue page stays fresh on its own. Say yes to both. No timer, nothing running in the background.
- **Level 2, autonomy**: everything above, plus the firing and cost caps, the review pipeline, and the launchd agent (macOS) or crontab line (Linux) that ticks the runner; with a webhook configured the same SessionEnd hook posts review evidence instead. It still asks before each piece that lives outside the repo.

Graduating is re-running `./install.sh` in the same checkout and picking level 2: your previous answers, including the level, pre-fill.

Running headless? `./install.sh --yes` accepts every default and writes only the in-repo config; a fresh clone defaults to level 1 (`--lite` pins it). The outside-the-repo steps are opt-in via `--with-commands`, `--with-launchd` and `--with-hooks` — `--with-launchd` implies level 2, while `--with-hooks` works at either level.

Then the first five minutes: run `claude` in any repo you are working on, `/add <project>` to queue it, do some work, `/wrap` to close out, and open `dashboard/index.html` in the legwork checkout. Come back another day with `/pickup <project>`. Verify the install any time with `python3 suite/legwork_runner.py --doctor`.

> **Make this repo your tracker.** A fresh clone gitignores `/projects/`, so your real queue stays out of any public fork by accident. When you want your queue in version control (the level 2 runner requires it; the verbs commit and push when it is there), point the checkout at a private remote of your own and delete the `/projects/` and `/dashboard/index.html` lines from `.gitignore`. [SETUP.md](SETUP.md) walks through it.

If you cloned somewhere other than `$HOME/legwork`, add `export LEGWORK_DIR=/path/to/your/clone` to your shell profile; the slash commands resolve the queue through that variable.

## What it costs

The manual loop bills nothing beyond the Claude Code sessions you were already running; the dashboard and the queue are local files. Level 2 spends real money while you are away: each fire is one headless session, each reviewed session adds one `claude -p` reviewer call, and the runner records the per-fire dollar cost in `runner.log`. Two caps guard it: `LEGWORK_DAILY_CAP` (fires per project per day, default 8) and `LEGWORK_DAILY_COST_CAP` (a dollar ceiling across all projects). The n8n review path bills an Anthropic API key separately.

## Level 2: the autonomous loop

The idea worth stealing is reviewer-by-exception. An LLM reviewer reads every autonomous session and only escalates to a human when a human decision is genuinely required: money, deploys, credentials, sending things to people, deleting data, or work that contradicts the stated intent. Everything else passes or is sent back for another pass. You stay in the loop by exception, not by babysitting.

Autonomy is opt-in per project, granted by `/vision`: an interview that captures a standing brief (north star, done means, guardrails, escalate when, taste) and only then sets `autonomy: loop`. The runner refuses projects without one.

<details>
<summary><strong>How the runner and reviewer work</strong></summary>

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

The runner ticks every five minutes, pulls the legwork repo, and fires every eligible project as an unattended (`claude -p`) session. Eligibility is strict: status `queued`, `autonomy: loop`, a `## Vision` section, no `blocked_on` line, a `repo:` that exists and is a clean git tree, a real next prompt, and both daily caps unspent. Sessions run with `--permission-mode acceptEdits` plus a narrow allowlist of git, mkdir, and the dashboard rebuild; anything more needs the target repo's own `.claude/settings.json`.

One requirement the manual loop does not have: the runner needs the legwork repo to have a writable remote (a private fork or even a local bare repo), because it pulls before every tick and pushes each claim so parallel machines and the review pipeline can never double-fire a project. `--dry-run`, `--doctor`, the dashboard and the manual loop all work without one.

Review is three options deep. Set `LEGWORK_LOCAL_REVIEW=1` and the runner triages each finished session itself with one `claude -p` call, writing the pass/revise/escalate verdict straight back to the project file: reviewer-by-exception with zero extra infrastructure. Point `LEGWORK_WEBHOOK_URL` at the optional n8n pipeline instead and reviews arrive as Telegram letters you can answer from your phone. With neither, the runner still fires and wraps; review is simply skipped.

</details>

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
