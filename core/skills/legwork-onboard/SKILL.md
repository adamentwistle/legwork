---
name: legwork-onboard
description: Set up legwork the way this user wants it. Interviews them about the optional pieces (the autonomy runner and its reviewer, house rules), then writes the config and installs only what they chose. Use for /onboard, "set up legwork", "install legwork", "configure legwork", "turn on the runner", or a re-run to change an earlier choice.
---

# Onboard

You set legwork up for one user. The queue always comes first; the runner
and house rules are optional. Ask about each, install only what they
choose, and leave a setup they can re-run to change any answer.

| Piece | What it is | Needs |
|---|---|---|
| Queue (always) | `projects/*.md`, the `/add` `/wrap` `/pickup` `/go` `/log` `/shelve` `/vision` commands, the tracker skill, the dashboard | nothing |
| Runner | a timer fires headless sessions for projects with a Vision and `autonomy: loop`, plus an optional reviewer | a checkout, `claude` on PATH |
| House rules | rules the tracker follows whenever a command updates a project: authorship, spelling, style | nothing |

## 1. Find out what is there (no questions yet)

Look these up yourself; never ask the user for a fact you can read:

- The legwork checkout: the current repo if it has `core/` and
  `install.sh`, else `$LEGWORK_DIR`, else `~/legwork`. If none of those is
  a checkout, legwork is installed as a plugin only. The queue works as it
  is; the runner and the self-rebuilding dashboard need a checkout, so
  offer to clone the legwork repo (the plugin's marketplace source) to
  `~/legwork` and continue there once they say yes.
- The legwork repo, where `config` and `house-rules.md` live:
  `$LEGWORK_DIR` if set, else the checkout. If it is unset and the
  checkout is not `~/legwork`, the user must add
  `export LEGWORK_DIR=<checkout>` to their shell profile; say so in step 5.
- An existing `config` and `house-rules.md` there. On a re-run, each
  question below defaults to what they already hold.
- The interpreter, `<python>` below: the first of `python3` and `python`
  for which `<name> -c 'import sys'` exits 0. On macOS and Linux that is
  `python3`. On Windows it is `python`: `python3` there is usually the
  Microsoft Store stub, which exits 9009 without running anything.
- The Claude config dir: `$CLAUDE_CONFIG_DIR`, else `~/.claude`.
- Whether `claude` is on PATH (`command -v claude`).

Tell the user in two or three lines what you found, then ask.

## 2. Ask, one question at a time

Use the AskUserQuestion tool when you have it, with your recommendation
first. Skip a question when an earlier answer makes it moot, and say why
in one line. Record every answer as you go.

1. **Runner.** "Do you want legwork to run projects on its own, on a
   timer?" Explain in one line: only projects they grant with `/vision`
   ever fire. If yes, ask the review mode (local reviewer, recommended; an
   n8n pipeline; none), the daily fire cap (default 8) and an optional
   daily cost cap. Recommend no for a first install: the manual loop is the
   whole product, and the runner is a re-run away.
2. **House rules.** "Any rules the tracker should follow when it updates a
   project?" Offer the common ones as a multi-select, then ask for their
   own: authored as you with no AI attribution or co-author lines;
   spellings to enforce (a product or company name); words or punctuation
   to avoid; mark every result OBSERVED, STALE or NOT RUN; lead
   escalations with the decision. `house-rules.example.md` shows the
   format.

## 3. Show the plan, then confirm once

One screen: every setting with its value, every file that will be
written, and where. Mark the ones outside the checkout (the Claude config
dir, `settings.json`, the launchd agent or crontab).
Ask once: "Install this?" Nothing is written before a yes.

## 4. Install

Run these from the checkout, in order. Show each command before running
it.

1. House rules, if they gave any: write `house-rules.md` in the legwork
   repo, one `- ` list item per rule, under a `# House rules` heading. On a
   re-run, keep every item already there that they did not ask to drop.
2. Settings: in the legwork repo's `config` (create it if it is
   missing), set `LEGWORK_DIR` to the legwork repo, and with the runner
   `LEGWORK_DAILY_CAP`, `LEGWORK_DAILY_COST_CAP`, and
   `LEGWORK_LOCAL_REVIEW=1` or `LEGWORK_WEBHOOK_URL` / `LEGWORK_ALERT_URL`,
   one `KEY=VALUE` line each, replacing an active line for that key. Leave
   every other line as it is.
3. The queue and runner: `./install.sh --yes --with-commands --with-hooks`,
   plus `--with-launchd` for the runner or `--lite` without it. The wizard
   pre-fills every value from the config step 2 wrote. When the legwork
   repo is a different checkout from this one, run this step there, since
   the wizard writes the config of the checkout it runs in. Never run it
   without `--yes`: it is an interactive wizard and will hang this session.

## 5. Check it, and hand over

- Re-read `config` and confirm every setting landed.
- With the runner: `<python> suite/legwork_runner.py --doctor` must exit 0.

Finish with one line per piece: installed, skipped, or waiting on the
user, each marked OBSERVED (you checked it) or NOT RUN. Then the first
thing to try: `/add` a project.

## Rules

- Never write outside the checkout before the confirm in step 3.
- Never install a piece the user did not choose, and never remove one they
  had; a re-run that turns the runner off says which files to delete
  instead.
- Never commit `config` or `house-rules.md`; both are gitignored.
- If a command fails, stop, show its output, and say what is installed so
  far. Do not retry with different flags without saying why.
