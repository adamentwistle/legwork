# Setup

This guide installs legwork, an autonomous project queue for Claude Code. By
the end you will have the legwork repo on disk, the runner firing on a timer,
and (optionally) the n8n review pipeline.

Read it in order. Steps 1 through 4 give you a working queue, runner and
dashboard with no n8n at all. Step 5 is the optional review pipeline.

## The one-command install

Most people should just run the wizard. From the cloned repo:

```
./install.sh
```

`./install.sh` is a thin wrapper around `scripts/legwork_install.py`, a
standard-library-only interactive installer. It walks the same ground as
steps 1 through 4 below: it prompts for every config value, writes `config`,
creates `projects/` and `.runner-logs/`, installs and loads the launchd agent
(macOS) or a crontab line (Linux), and registers the SessionStart/SessionEnd
hooks in your Claude `settings.json`. It asks before every action that touches
anything outside the repo, so you can decline any piece and do it by hand.

Flags: `--yes` accepts every default without prompting, `--no-color` prints
plainly. Re-running is safe: it reads your existing `config` to pre-fill the
prompts and never duplicates a launchd agent, crontab line or hook entry.

The rest of this guide is the manual path. Follow it if you skipped a piece of
the wizard, want to understand exactly what it did, or are wiring the optional
n8n review pipeline (step 5), which the wizard does not automate because it
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
- A git remote for the legwork repo, only if you want the optional review
  pipeline in step 5. The Telegram reply path writes decisions back through
  the GitHub Contents API, so it needs a GitHub remote. The local queue,
  runner and dashboard work with no remote.

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
block, an append-only `## Log`). The repo gitignores `/projects/`,
`/dashboard/index.html` and `/config`, so your real project data, the
generated dashboard and your local config never get committed to legwork
itself. Keep your own project files in a private repo if you want them
tracked.

For the file format, see `examples/projects/`. Those are invented sample
projects that show the frontmatter, Vision, prompt and Log layout. The full
spec is in `.claude/skills/legwork-tracker/SKILL.md`.

If you put the checkout somewhere other than `$HOME/legwork`, set
`LEGWORK_DIR` to that path in your config (step 2). Wherever the runner runs,
it expects to find `scripts/`, `projects/` and `config` under `LEGWORK_DIR`.

## 2. Config

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

## 3. The hooks

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

## 4. The runner

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

## 5. Optional: the n8n review pipeline

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

For the Telegram side (the GitHub fine-grained PAT, the n8n credential, and
activating the trigger), follow `reply-capture/SETUP.md`. The write-back token
is a fine-grained, repo-scoped PAT held only as an n8n credential, never in
the repo.

## Verify

Run these from your legwork repo to confirm the install:

```
python3 -m unittest discover -s tests
```

The full stdlib test suite (127 tests) should pass.

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
