# Setup

This guide installs legwork, an autonomous project queue for Claude Code. By
the end you will have the legwork repo on disk, the runner firing on a timer,
and (optionally) the n8n review pipeline.

Read it in order. Steps 1 and 2 give you the manual loop: the queue, the
slash commands and the dashboard, with nothing running on a timer. Steps 3
through 5 add the level 2 runner. Step 6 is the optional n8n review pipeline.

## The one-command install

Most people should just run the wizard. From the cloned repo:

```
./install.sh
```

`./install.sh` is a thin wrapper around `scripts/legwork_install.py`, a
standard-library-only interactive installer. It walks the same ground as
steps 1 through 5 below: it prompts for every config value, writes `config`,
creates `projects/` and `.runner-logs/`, copies the slash commands and the
legwork-tracker skill into user-level `~/.claude`, installs and loads the
launchd agent (macOS) or a crontab line (Linux), and registers the
SessionStart/SessionEnd hooks in your Claude `settings.json`. It asks before
every action that touches anything outside the repo, so you can decline any
piece and do it by hand.

Flags: `--yes` accepts every default without prompting, but the steps that
touch things outside the repo (the user-level command install, the
launchd/cron timer and the Claude hooks) are skipped unless you add
`--with-commands`, `--with-launchd` or `--with-hooks`, so a headless `--yes`
install never writes to `~/.claude`, loads a launchd agent or edits your
`settings.json` behind your back. `--no-color` prints plainly. Re-running is
safe: it reads your existing `config` to pre-fill the prompts, refreshes the
command copies, and never duplicates a launchd agent, crontab line or hook
entry.

The rest of this guide is the manual path. Follow it if you skipped a piece of
the wizard, want to understand exactly what it did, or are wiring the optional
n8n review pipeline (step 6), which the wizard does not automate because it
lives in your n8n instance, not on this machine.

## Requirements

- `python3`. Version 3.9 or newer is fine. Everything in `scripts/` is
  standard library only, no pip installs. On macOS the launchd agent runs the
  runner with `/usr/bin/python3`, the system interpreter, so it does not
  depend on a shell-managed Python.
- The Claude Code CLI (`claude`) on your `PATH`. The runner shells out to it
  for each headless session.
- `git`. The runner pulls and commits the legwork repo, and it only fires a
  project when the target repo is a clean git tree.
- A writable git remote for the legwork repo, only for level 2: the runner
  pulls before every tick and pushes each claim, so it needs a remote it can
  push to (a private fork, or even a local bare repo). The Telegram reply
  path additionally needs that remote to be on GitHub, since it writes
  decisions back through the GitHub Contents API. The manual loop, the
  dashboard, `--dry-run` and `--doctor` all work with no remote.

## 1. The legwork repo

legwork is the legwork repo. The runner, the dashboard builder and the
config file all live inside it, alongside your project files and the
generated dashboard. The default location is `$HOME/legwork`. Put the legwork
checkout there, or put it anywhere and point `LEGWORK_DIR` at it.

```
git clone <your-legwork-remote> "$HOME/legwork"
cd "$HOME/legwork"
mkdir -p projects
```

The `projects/` directory is the source of truth: one markdown file per
project (frontmatter, an optional `## Vision`, a `## Next prompt` fenced
block, an append-only `## Log`). For the file format, see
`examples/projects/`. Those are invented sample projects that show the
frontmatter, Vision, prompt and Log layout. The full spec is in
`.claude/skills/legwork-tracker/SKILL.md`.

### Make this repo your tracker

The stock `.gitignore` ignores `/projects/`, `/dashboard/index.html` and
`/config`, so a fresh clone can never leak your real queue into a public
fork by accident. That default is right for trying legwork out, and wrong
the moment this checkout becomes your actual tracker: the level 2 runner
commits and pushes project files on every claim and wrap, and the verbs
(`/wrap`, `/vision`) commit them too when they can. When you are ready:

1. Point the checkout at a private remote you control
   (`git remote set-url origin <your-private-remote>`), or clone your
   private fork in the first place.
2. Delete the `/projects/` and `/dashboard/index.html` lines from
   `.gitignore` (the comment above them says the same). Leave `/config`
   ignored; it can hold webhook URLs.
3. Commit and push, and your queue is versioned from here on.

Until you do this, project files still work; they just live untracked on
this machine only, and the runner cannot fire.

If you put the checkout somewhere other than `$HOME/legwork`, set
`LEGWORK_DIR` to that path in your config (step 3) and also export it from
your shell profile (`export LEGWORK_DIR=/path/to/your/clone`): the config
file is read by the runner, while the slash commands resolve the queue
through the environment variable.

## 2. The commands and the skill

The manual loop is six slash commands (`/add`, `/wrap`, `/pickup`, `/vision`,
`/log`, `/shelve`) plus the legwork-tracker skill they share. They ship in
this repo's `.claude/`, which means a fresh clone only has them inside the
checkout itself; a `/wrap` at the end of a session in one of your own repos
would find nothing. Install them user-level so they work from any repo:

```
mkdir -p ~/.claude/commands ~/.claude/skills
cp .claude/commands/*.md ~/.claude/commands/
cp -R .claude/skills/legwork-tracker ~/.claude/skills/
```

This is the same thing the wizard's command step does. The commands find the
queue via `$LEGWORK_DIR` (falling back to `~/legwork`), which is why step 1
has you export it when the checkout lives elsewhere. Re-copy after pulling a
legwork update; the wizard refreshes the copies on re-run.

## 3. Config

Copy the template and edit it:

```
cp config.example config
```

`config` is gitignored. `scripts/legwork_runner.py` reads it at startup via
`load_config()`, so launchd (which does not read your shell profile), cron and
manual runs all share one source of truth. The file is `KEY=VALUE` lines;
`#` starts a comment; `$HOME` and `~` are expanded.

Real environment variables always win over the file. If a variable is already
set in the environment, the value in `config` is ignored. You can also point
the runner at a config elsewhere with `LEGWORK_CONFIG=/path/to/config`.

The defaults are sensible for a first run. `LEGWORK_DIR` defaults to
`$HOME/legwork` and `LEGWORK_DAILY_CAP` defaults to 8 fires per project per
day. The two webhook URLs are optional and commented out; leave them unset
for now. For every variable, see `CONFIG.md`.

## 4. The hooks

Two Claude Code hooks feed the review pipeline. Register them in the
`settings.json` of the Claude config the runner uses. If you set
`CLAUDE_CONFIG_DIR` (or a per-account `CLAUDE_CONFIG_DIR_<NAME>`) in your
config, that is the config dir whose `settings.json` needs them. If you left
`CLAUDE_CONFIG_DIR` unset, autonomous sessions inherit your default config, so
register them in `~/.claude/settings.json`.

- `scripts/session_start_hook.sh` (SessionStart): records the repo HEAD for
  the session, so the end hook can report only what this session changed.
- `scripts/session_end_hook.sh` (SessionEnd): gathers session-scoped git
  evidence plus the project's tracker entry and POSTs them to
  `LEGWORK_WEBHOOK_URL`.

Add this to the `settings.json` of that config dir, with `$HOME/legwork`
replaced by your `LEGWORK_DIR` if it differs:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/legwork/scripts/session_start_hook.sh"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/legwork/scripts/session_end_hook.sh"
          }
        ]
      }
    ]
  }
}
```

The SessionEnd hook is a no-op when `LEGWORK_WEBHOOK_URL` is unset: it logs a
skip to `$LEGWORK_DIR/hook.log` and exits. So registering the hooks now is
harmless even if you never set up the pipeline. Sessions that end via clear or
resume are also skipped, since they are restarting, not finishing.

## 5. The runner

The runner ticks on a timer. Each tick fires every eligible project as one
headless `claude -p` session, then wraps. Pick launchd on macOS or cron on
Linux.

### (a) launchd on macOS

`scripts/com.legwork.runner.plist` is a template with two placeholders:
`__LEGWORK_DIR__` (the absolute path to your legwork repo) and `__PYTHON__`
(the interpreter, normally `/usr/bin/python3`). Fill them in, drop the result
in `~/Library/LaunchAgents/`, and load it:

```
sed -e 's#__LEGWORK_DIR__#'"$HOME"'/legwork#g' \
    -e 's#__PYTHON__#/usr/bin/python3#g' \
    scripts/com.legwork.runner.plist > ~/Library/LaunchAgents/com.legwork.runner.plist
launchctl load ~/Library/LaunchAgents/com.legwork.runner.plist
```

The agent runs at load and then every `StartInterval` seconds. The template
ships with `StartInterval` set to 300 (every 5 minutes); edit that value in
the plist to change the tick interval. Runner output goes to
`$LEGWORK_DIR/.runner-logs/launchd.log`.

To stop it:

```
launchctl unload ~/Library/LaunchAgents/com.legwork.runner.plist
```

### (b) cron on Linux

Add a crontab line that runs the runner every 5 minutes:

```
*/5 * * * * /usr/bin/python3 $HOME/legwork/scripts/legwork_runner.py >> $HOME/legwork/.runner-logs/cron.log 2>&1
```

The cron schedule is the tick interval; change `*/5` to run more or less
often. Make sure `.runner-logs/` exists (`mkdir -p $HOME/legwork/.runner-logs`)
and that `claude` is on the `PATH` cron uses.

Overlapping ticks are safe: a lock file makes a second tick exit quietly while
one is in flight.

## 6. Optional: the n8n review pipeline

This step is optional. Everything above runs the queue, the runner and the
dashboard with no n8n. With neither `LEGWORK_WEBHOOK_URL` nor
`LEGWORK_ALERT_URL` set, the runner still fires sessions and they still wrap;
the review post and the Telegram alerts are simply skipped.

The pipeline is three importable n8n workflows. Import them into your own n8n
instance:

- `reviewer/n8n-review-workflow.json`: the reviewer. Takes the session
  evidence and returns pass / revise / escalate.
- `reply-capture/n8n-reply-capture-workflow.json`: the Telegram reply path.
  Reply to a review letter, or send slash commands, to drive the queue from
  your phone.
- `alerts/n8n-alerts-workflow.json`: runner stall alerts and a daily
  heartbeat.

After import:

1. Fill the `REPLACE_WITH_` placeholders in each workflow: n8n credential ids,
   your Telegram chat id, and the GitHub `owner/repo` of your legwork repo.
   The committed JSON never carries real secrets.
2. Paste `reviewer/n8n-build-node.js` into the "Build review request" node.
   That file is the source of truth for the review rubric. The reviewer model
   comes from `REVIEWER_MODEL` (default `claude-sonnet-4-6`), applied in that
   node. `reviewer/rubric.md` is the readable mirror.
3. Set the webhook URLs in your `config`:

   ```
   LEGWORK_WEBHOOK_URL=https://your-n8n-host/webhook/legwork-review
   LEGWORK_ALERT_URL=https://your-n8n-host/webhook/legwork-alert
   ```

   `LEGWORK_WEBHOOK_URL` is the review post the SessionEnd hook and the runner
   send to. `LEGWORK_ALERT_URL` receives stall alerts and the heartbeat.

For the Telegram side (the bot and its credential, the GitHub fine-grained
PAT, the Anthropic key, restricting the trigger to your own Telegram user
id, and activating), follow `reply-capture/SETUP.md`. The write-back token
is a fine-grained, repo-scoped PAT held only as an n8n credential, never in
the repo.

## Verify

Run these from your legwork repo to confirm the install:

```
python3 -m unittest discover -s tests
```

The full stdlib test suite should pass.

```
python3 scripts/build_dashboard.py
```

This regenerates `dashboard/index.html` from the top-level `projects/*.md`.
With an empty `projects/` it builds an empty dashboard. The samples under
`examples/projects/` are reference only and are not picked up by the builder;
copy one into `projects/` if you want to see it on the dashboard. The html is
a build artifact and is gitignored.

```
python3 scripts/legwork_runner.py --dry-run
```

This prints each project and why it is or is not eligible to fire, and changes
nothing. A fresh queue with no `autonomy: loop` projects will show every
project skipped, which is correct: autonomy is opt-in per project, granted by
a human via `/vision` or the Telegram `/loop` command. Drop `--dry-run` to run
one real tick by hand.

## Troubleshooting

- `python3 scripts/legwork_runner.py --doctor` is the first stop: it checks
  the config, the repo layout, the `claude` binary, the git state and the
  review mode, and says what is wrong in plain lines.
- `$LEGWORK_DIR/runner.log` is the audit trail of every tick: what fired,
  what was skipped and why, per-fire cost, and review verdicts.
- `$LEGWORK_DIR/hook.log` records every SessionStart/SessionEnd hook firing
  and skip, including the webhook POST result.
- `$LEGWORK_DIR/.runner-logs/` holds the timer's own stdout
  (`launchd.log` or `cron.log`) and per-session transcripts.
- The slash commands not found in your own repos? They only ship inside this
  checkout; install them user-level (step 2). Commands finding no queue?
  Export `LEGWORK_DIR` in your shell profile (step 1).
- The runner assessing a project eligible but never firing it? Check the
  legwork repo has a writable remote and that `/projects/` is no longer
  gitignored ("Make this repo your tracker", step 1).
- Touch `$LEGWORK_DIR/.runner-pause` to stop all firing immediately without
  uninstalling anything; delete it to resume.

## Uninstall

Everything legwork installs outside the repo is one timer, one settings
entry and the copied commands; remove them and the checkout is just a
directory you can delete.

```
# macOS: unload and remove the timer
launchctl unload ~/Library/LaunchAgents/com.legwork.runner.plist
rm ~/Library/LaunchAgents/com.legwork.runner.plist

# Linux: remove the marker-tagged crontab line
crontab -l | grep -v "# legwork runner" | crontab -

# the user-level commands and skill
rm ~/.claude/commands/{add,wrap,pickup,vision,log,shelve}.md
rm -r ~/.claude/skills/legwork-tracker
```

Then open the `settings.json` you registered the hooks in (`~/.claude/` or
your dedicated `CLAUDE_CONFIG_DIR`) and remove the two entries whose
`command` ends in `session_start_hook.sh` / `session_end_hook.sh`. Finally
delete the checkout, which takes `config`, `projects/` and every log with
it; if you made the repo your tracker, your queue also lives on your private
remote.
