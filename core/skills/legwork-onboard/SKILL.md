---
name: legwork-onboard
description: Set up legwork the way this user wants it. Interviews them about each optional layer (the autonomy runner, shipping commands, the fanout orchestrator, Herdr, a Hermes or manual switchboard, Linear, the usage guard, house rules), then writes the config and installs only what they chose. Use for /onboard, "set up legwork", "install legwork", "configure legwork", "turn on the runner/orchestration/switchboard", or a re-run to change an earlier choice.
---

# Onboard

You set legwork up for one user. Legwork is layers, and each one past the
first is optional. Ask about each layer, install only what they choose, and
leave a setup they can re-run to change any answer.

| Layer | What it is | Needs |
|---|---|---|
| Queue (always) | `projects/*.md`, the `/add` `/wrap` `/pickup` `/go` `/log` `/shelve` `/vision` commands, the tracker skill, the dashboard | nothing |
| Runner | a timer fires headless sessions for projects with a Vision and `autonomy: loop`, plus an optional reviewer | a checkout, `claude` on PATH |
| Shipping | `/ship` (commit, push, PR, Bugbot, wrap) and `/bugbot` (the review loop) | `gh` signed in |
| Orchestration | `/fanout` runs one project's roadmap to many PRs; the switchboard protocol has it report `DECISION`s instead of stopping | shipping |
| Herdr | panes the orchestrator can start, watch and steer; strongly recommended | Herdr installed |
| Switchboard | who relays an orchestrator's decisions: a Hermes thread, or the user by hand | orchestration; Hermes needs Herdr |
| Linear | an issue per change, kept in step with its PR | the Linear MCP |
| Usage guard | a hook that makes long runs pause at a safe point before a usage limit | a usage cache (ccstatusline) |
| House rules | rules every worker follows: authorship, attribution, spelling, style | nothing |

## 1. Find out what is there (no questions yet)

Look these up yourself; never ask the user for a fact you can read:

- The legwork checkout: the current repo if it has `core/` and
  `orchestration/install.py`, else `$LEGWORK_DIR`, else `~/legwork`. If
  none of those is a checkout, legwork is installed as a plugin only. The
  queue layer works as it is; every other layer needs a checkout, so offer
  to clone the legwork repo (the plugin's marketplace source) to
  `~/legwork` and continue there once they say yes.
- The legwork repo, where `config` and `house-rules.md` live and the
  skills look: `$LEGWORK_DIR` if set, else the checkout. If `$LEGWORK_DIR`
  is set and is not the checkout, that repo holds the queue; settings and
  house rules go there, and the scripts still run from the checkout (the
  installer writes their full path into every skill). If it is unset and
  the checkout is not `~/legwork`, the user must add
  `export LEGWORK_DIR=<checkout>` to their shell profile; say so in step 5.
- An existing `config` and `house-rules.md` there. On a re-run, each
  question below defaults to what they already hold.
- The Claude config dir: `$CLAUDE_CONFIG_DIR`, else `~/.claude`.
- Tools: `command -v claude gh herdr hermes`, `gh auth status`, and
  whether `HERDR_ENV` is set (this session runs inside Herdr).
- A usage cache: `~/.cache/ccstatusline/usage.json`, or the path in
  `LEGWORK_USAGE_CACHE`.
- The GitHub owner of the user's repos: `gh api user -q .login` and
  `gh api user/orgs -q '.[].login'`, as suggestions for question 6.

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
2. **Shipping.** "Install `/ship` and `/bugbot`?" Then: "Which GitHub
   users or orgs have Cursor Bugbot installed?" (none is a fine answer).
3. **Orchestration.** "Do you want `/fanout`, one orchestrator working a
   project's roadmap into many PRs, reporting decisions instead of
   stopping?" Needs shipping; if they said no to shipping, say so and offer
   both.
4. **Herdr.** Only with orchestration. If `herdr` is missing, recommend it
   strongly: with Herdr, workers run in panes the user can watch and steer
   in the morning, and the switchboard can open, cycle and resume
   orchestrators. Without it, `/fanout` uses subagents only and the
   switchboard cannot touch panes. Point them at herdr.dev to install it,
   and ask whether to wait for that or carry on without. If `herdr` is
   there, offer to install its skill (`herdr --skill`).
5. **Switchboard.** Only with orchestration. "Who relays an orchestrator's
   decisions to you?" A) Hermes: one Hermes thread watches every
   orchestrator, puts decisions in front of you, relays your answer and
   cycles context (needs Herdr and Hermes). B) By hand: you read
   `.legwork/switchboard.md` (or `switchboard-wait --status <repo>:none`)
   and type answers into the pane. Recommend A when both tools are there,
   else B. For Hermes, ask which profile (`default` or a named one).
6. **Your repos.** Only with shipping or orchestration. "Which GitHub users
   or orgs are yours?" Suggest the ones `gh` reported. On these,
   orchestrators may merge when told to, put issue ids in PRs and push
   planning docs; every other owner is treated as shared.
7. **Linear.** Only with orchestration. "Track each change as a Linear
   issue?" If yes: the team name, its issue key (the PLT in PLT-12), and
   any labels worth sweeping at planning time.
8. **Usage guard.** Only with orchestration, and only when a usage cache
   exists (if none, say the guard would stay silent and skip it). "Install
   the hook that pauses long runs before a usage limit?"
9. **Your own skills.** Only with shipping or orchestration. "Do you have
   a skill that writes your PR bodies, or one that checks a UI in a
   browser?" Read the installed skill names in the Claude config dir and
   offer them. They become `LEGWORK_PR_SKILL` and `LEGWORK_UI_SKILL`.
10. **House rules.** "Any rules every worker must follow?" Offer the common
   ones as a multi-select, then ask for their own: authored as you with no
   AI attribution or co-author lines; never push main, always a PR;
   spellings to enforce (a product or company name); words or punctuation
   to avoid; mark every result OBSERVED, STALE or NOT RUN; lead escalations
   with the decision. `house-rules.example.md` shows the format.

## 3. Show the plan, then confirm once

One screen: every setting with its value, every file that will be
written, and where. Mark the ones outside the checkout (the Claude config
dir, the Hermes profile, `settings.json`, the launchd agent or crontab).
Ask once: "Install this?" Nothing is written before a yes.

## 4. Install

Run these from the checkout, in order, with `--legwork-dir <legwork repo>`
on every `orchestration/install.py` call. Show each command before running
it.

1. House rules, if they gave any: write `house-rules.md` in the legwork
   repo, one `- ` list item per rule, under a `# House rules` heading.
2. Settings: `python3 orchestration/install.py --set KEY=VALUE ...` with
   every setting they chose:
   - runner: `LEGWORK_DAILY_CAP`, `LEGWORK_DAILY_COST_CAP`, and
     `LEGWORK_LOCAL_REVIEW=1` or `LEGWORK_WEBHOOK_URL` / `LEGWORK_ALERT_URL`
   - `LEGWORK_BUGBOT_OWNERS`, `LEGWORK_OWN_OWNERS`
   - `LEGWORK_SWITCHBOARD=hermes` or `manual`
   - `LEGWORK_LINEAR_TEAM`, `LEGWORK_LINEAR_TEAM_KEY`,
     `LEGWORK_LINEAR_SWEEP_LABELS`
   - `LEGWORK_PR_SKILL`, `LEGWORK_UI_SKILL`
   Values with spaces go in quotes on the command line; the file keeps
   them as written.
3. The queue and runner: `./install.sh --yes --with-commands --with-hooks`,
   plus `--with-launchd` for the runner or `--lite` without it. The wizard
   pre-fills every value from the config step 2 wrote and keeps the
   orchestration settings. When the legwork repo is a different checkout
   from this one, run this step there, since the wizard writes the config
   of the checkout it runs in. Never run it without `--yes`: it is an
   interactive wizard and will hang this session.
4. The optional layer: `python3 orchestration/install.py --with <pieces>`,
   where the pieces are the ones they chose: `shipping`, `fanout`,
   `switchboard` (always with fanout), `herdr-skill`, `usage-guard`,
   `hermes` (with `--hermes-profile <name>`). Run it with `--dry-run` first
   when anything already exists at a destination, and show what it would
   overwrite.

## 5. Check it, and hand over

- Re-read `config` and confirm every setting landed. Run
  `python3 orchestration/install.py --with <pieces> --dry-run` and confirm
  each destination now exists.
- With orchestration: `orchestration/bin/switchboard-wait --status
  <checkout>:none` must exit 0.
- With Hermes, these steps are the user's, because they change Hermes
  itself. List them, do not run them:
  - import the Claude skills a planning session uses into the profile:
    `hermes -p <profile> import-agent claude-code --source <claude dir> --yes`
  - set `prompt_caching.cache_ttl: 1h` in the profile, so a thread that
    wakes every 30 minutes reuses its cache
  - run the 15 minute plumbing test in `orchestration/docs/smoke-test.md`
- In every repo an orchestrator will work in: `.legwork/` gitignored, and
  the allow and deny rules in `orchestration/docs/hermes-setup.md`
  ("Permissions") in its gitignored `.claude/settings.local.json`.

Finish with one line per layer: installed, skipped, or waiting on the user,
each marked OBSERVED (you checked it) or NOT RUN. Then the first thing to
try: `/add` a project, or for orchestration the first run in
`orchestration/docs/first-run.md`.

## Rules

- Never write outside the checkout before the confirm in step 3.
- Never install a layer the user did not choose, and never remove one they
  had; a re-run that turns a layer off says which files to delete instead.
- Never commit `config` or `house-rules.md`; both are gitignored.
- If a command fails, stop, show its output, and say what is installed so
  far. Do not retry with different flags without saying why.
