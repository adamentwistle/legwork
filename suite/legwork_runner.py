#!/usr/bin/env python3
"""Fire queued legwork prompts as headless Claude Code sessions.

Zero dependencies, like everything under core/ and suite/. Run by launchd
every five minutes (com.legwork.runner), or by hand:

    python3 suite/legwork_runner.py            one tick
    python3 suite/legwork_runner.py --dry-run  show eligibility, change nothing
    python3 suite/legwork_runner.py --doctor   preflight checklist, change nothing

One tick fires every eligible project at once, one session in flight per
project: target-repo sessions run fully parallel in worker threads, while
every legwork-repo write (claim, repair, dashboard rebuild, audit window)
serialises behind an in-process lock and ends pushed, so parallel wraps and
n8n remote commits cannot race the runner. A claimed project reads
status: running, so later ticks skip it while its session is in flight.
Two projects sharing one repo never fire in the same window; the oldest
claim wins. A lock file still makes overlapping ticks exit quietly.

A project is eligible only when ALL of these hold:
  - status: queued
  - autonomy: loop in the frontmatter (explicit human opt-in, set via /vision)
  - the file has a ## Vision section (the standing brief that stands in for
    the human; autonomy without a vision is refused)
  - EXCEPTION: fire_once in the frontmatter (set only by the human, via the
    Telegram /fire command) stands in for both of the above for exactly one
    session. It is the human hand-firing the minted prompt; the claim that
    flips queued to running consumes the key. Every other guard still holds.
  - repo: points at an existing git repository with a clean tree
  - the Next prompt is a real prompt, not a Human action / DECISION NEEDED /
    PROMPT NEEDED marker
  - fewer than DAILY_CAP fires for this project today (runner.log is counted)

Permissions: sessions run headless with acceptEdits (file edits auto-accept
in the project repo and the legwork repo) plus an allowlist of git, mkdir
and the dashboard rebuild, which is just enough to work, commit and /wrap.
Anything more (test runners, builds, deploys) is granted per repo by the
human, in that repo's own .claude/settings.json allow rules; everything else
is denied and the session is expected to say so and wrap honestly. No
permission checks are bypassed. A per-session --settings file denies the
Edit/Write tools on the legwork control plane (core/, suite/ and scripts/);
Bash is not covered, so the deny blocks the direct edit path
while audit_session_window detects committed control-plane touches post-hoc.
SECURITY.md spells out the boundary honestly.

Accounts: when CLAUDE_CONFIG_DIR is set, sessions fire under that config dir
so an autonomous run never inherits whatever account your interactive shell
defaults to; leave it unset to use the default config. A project may name an
account in its frontmatter (account: <name>), which maps to
CLAUDE_CONFIG_DIR_<NAME> for a dedicated config dir. If a session's
SessionEnd hook did not fire (for instance an account whose config carries
no hooks), the runner posts the review request itself; the loop closes
either way.

Safety valves: touch .runner-pause in the legwork repo to stop all firing;
delete it to resume. The tracked twin .runner-pause-remote does the same and
exists so the Telegram /pause and /resume commands can commit and delete it
through the Contents API; it is checked again right after the pull so a fresh
pause lands on the very next tick. runner.log is the audit trail. Sessions
that exit without wrapping are flipped to review, and the reviewer webhook
is told; the exception is a session that died on a transient API error
(529 overloaded, rate limit, 5xx) with zero turns and zero cost, which is
re-queued quietly so a later tick retries it, still bounded by DAILY_CAP.
A re-queued project then waits out an escalating backoff (TRANSIENT_BASE,
doubling per consecutive transient crash up to TRANSIENT_CAP) before it
fires again, so a cloud outage is not hammered every five minutes; a clean
fire clears the count. A session killed by a usage limit defers its whole
account until the named reset (or USAGE_BLOCK_DEFAULT when none is given),
so sibling projects on that account do not fire straight into the same wall.
This backoff and usage state lives in .runner-state.json.

Observability: the runner posts plain text to the alerts webhook
(LEGWORK_ALERT_URL, a small n8n workflow that forwards to Telegram) when
ticking has been blocked for more than STALL_ALERT_AFTER seconds, and once a
day after HEARTBEAT_HOUR it sends a heartbeat: last fire, eligibility per
autonomy project, stale running projects, escalated count. Runtime state
lives in .runner-state.json (gitignored). After every fire it audits the
legwork repo: commits in the session window that touch anything outside
projects/ or dashboard/ raise an alert, because the legwork repo is the
control plane and quiet edits to it are how autonomy goes wrong. Sessions
run with --output-format stream-json, so .runner-logs/ holds real
transcripts and runner.log records cost per fire.

Prompt directives: a minted prompt may carry "Model: haiku|sonnet|opus"
and "Effort: low|medium|high|xhigh|max" lines. The runner strips them from
the prompt and passes --model / --effort, so each task runs on the right
tier. A project with blocked_on in its frontmatter is never fired; clear the
key when the blocker lifts. Ticking proceeds over untracked files in the
legwork repo. Uncommitted tracked changes would break the pull and could be
swept into commits, so they block firing, with one exception: a tracker-only
edit (everything dirty is under projects/, the shape a manual work-account
wrap leaves behind with no hook to commit it) is committed by the runner
itself and the tick proceeds. Any dirty path outside projects/ still blocks
until a human commits or reverts.

Config: LEGWORK_DIR, the webhook URLs, CLAUDE_CONFIG_DIR, LEGWORK_DAILY_CAP
and the optional spend guard LEGWORK_DAILY_COST_CAP (dollars; unset or 0 means
no cap) are read from the environment, and from a `config` file
beside the repo root when one is present (real environment variables win),
so launchd and manual runs share one source of truth. See config.example.
The review pipeline is optional: with neither LEGWORK_WEBHOOK_URL nor
LEGWORK_ALERT_URL set, the runner still fires sessions and they still wrap,
it just skips the review post and the Telegram alerts. As an alternative to
the n8n webhook, set LEGWORK_LOCAL_REVIEW to triage each session in-process
with a `claude -p` call (REVIEWER_MODEL, default claude-sonnet-4-6) and write
the pass/revise/escalate verdict straight back to the project file, so the
reviewer-by-exception loop runs with no n8n; see suite/legwork_review.py.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# suite/ imports from core/, never the reverse. A direct run (or launchd)
# puts only suite/ at sys.path[0], so core/, the shared base, goes right
# after it before the legwork imports below resolve.
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "core"))

import legwork_review  # noqa: E402
from legwork_common import (  # noqa: E402
    COST_RE, PROMPT_RE, days_since, legwork_dir, load_config,
    parse_frontmatter, python_exe, write_lf)

load_config()

LEGWORK_DIR = legwork_dir()
PROJECTS_DIR = LEGWORK_DIR / "projects"
RUNNER_LOG = LEGWORK_DIR / "runner.log"
TRANSCRIPTS = LEGWORK_DIR / ".runner-logs"
LOCK_FILE = LEGWORK_DIR / ".runner.lock"
# Per-session Claude settings the runner writes and passes with --settings:
# deny rules that block a fired session from editing the control plane. Kept
# in the temp dir, not the legwork repo, since it is ephemeral session config
# regenerated every tick and should never show up in the repo's git status.
GUARD_SETTINGS = Path(tempfile.gettempdir()) / "legwork-runner-guard.json"
PAUSE_FILE = LEGWORK_DIR / ".runner-pause"
# Tracked twin of the pause file, committed and deleted by the Telegram
# /pause and /resume commands through the GitHub Contents API.
REMOTE_PAUSE_FILE = LEGWORK_DIR / ".runner-pause-remote"

# Review pipeline endpoints. Both OPTIONAL: leave unset to run the queue with
# no reviewer. With no LEGWORK_WEBHOOK_URL the runner still fires and wraps,
# it just skips the review post; with no LEGWORK_ALERT_URL it skips the
# Telegram alerts and heartbeat. Set them via the environment or config.
WEBHOOK_URL = os.environ.get("LEGWORK_WEBHOOK_URL", "")
ALERT_URL = os.environ.get("LEGWORK_ALERT_URL", "")
# Local reviewer: the zero-dependency equivalent of the n8n review pipeline.
# Opt in with LEGWORK_LOCAL_REVIEW; it runs only when no LEGWORK_WEBHOOK_URL is
# set, so n8n stays the path when wired and local is the no-n8n alternative.
# After a real-work session the runner triages it with a `claude -p` call and
# writes the verdict (pass/revise/escalate) straight back to the project file.
LOCAL_REVIEW = os.environ.get("LEGWORK_LOCAL_REVIEW", "").strip().lower() \
    in ("1", "true", "yes", "on")
# Reviewer model for both the n8n pipeline (read by n8n-build-node.js) and the
# local reviewer. A full id or a short alias; default matches the n8n copy.
REVIEWER_MODEL = os.environ.get("REVIEWER_MODEL", "claude-sonnet-4-6")
STATE_FILE = LEGWORK_DIR / ".runner-state.json"
# Default Claude config dir for autonomous sessions. Empty means inherit the
# default config. A project's account: <name> maps to CLAUDE_CONFIG_DIR_<NAME>
# (uppercased); see account_config_dir().
DEFAULT_CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR", "")
# Where to look for the claude binary when it is not on PATH. Windows has no
# /usr/local and spells the executable with an extension, so the POSIX names
# can never match there.
CLAUDE_FALLBACKS = (
    [Path.home() / ".local" / "bin" / "claude.exe"] if os.name == "nt" else
    [Path.home() / ".local/bin/claude",
     Path("/opt/homebrew/bin/claude"),
     Path("/usr/local/bin/claude")]
)

# Config values that failed to parse. Garbage in a cap must not raise at
# import and kill every tick with no log line; the bad value falls back to
# its default and is surfaced by tick() and --doctor instead.
CONFIG_WARNINGS = []


def _env_number(name, default, cast):
    """A numeric env/config value, or its default when unset, garbage or
    non-finite. Failures are recorded in CONFIG_WARNINGS, never raised."""
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        value = cast(raw)
    except ValueError:
        CONFIG_WARNINGS.append(
            f"{name}={raw!r} is not a number; using {default}")
        return default
    if not math.isfinite(value):
        CONFIG_WARNINGS.append(
            f"{name}={raw!r} is not finite; using {default}")
        return default
    return value


DAILY_CAP = _env_number("LEGWORK_DAILY_CAP", 8, int)  # fires per project per day
# Optional spend guard. Unset or 0 means no cost cap. When set, the runner
# sums today's per-fire costs from runner.log (the $X.XX transcript_summary
# logs) and stops firing for the rest of the day once the cap is reached.
DAILY_COST_CAP = _env_number("LEGWORK_DAILY_COST_CAP", 0.0, float)
SESSION_TIMEOUT = 3600   # seconds before a session is terminated
GRACE = 60               # seconds between SIGTERM and SIGKILL
# A single tick should never outlive one session. Past this a live-PID lock
# means the runner itself is wedged (a hung git/network call or a deadlock),
# so the lock is reclaimed and an alert is sent instead of stalling forever.
LOCK_MAX_AGE = SESSION_TIMEOUT + GRACE + 300
HOOK_GRACE = 10          # seconds for the async SessionEnd hook to land
STALL_ALERT_AFTER = 1800 # seconds of blocked ticks before one Telegram alert
HEARTBEAT_HOUR = 8       # daily pulse goes out on the first tick after this
# After a transient cloud crash a project waits before it fires again, so a
# 529 storm is not hammered every five minutes. The wait doubles per
# consecutive transient crash (15 min, 30, 60, ...) up to the cap, and a
# clean fire clears the count.
TRANSIENT_BASE = 900     # first backoff after a transient crash (15 min)
TRANSIENT_CAP = 7200     # ceiling on the transient backoff (2 h)
# When a session dies on a usage limit, the whole account is deferred until
# the named reset; if no reset clock can be read, this default block applies.
USAGE_BLOCK_DEFAULT = 1800  # 30 min

VISION_RE = re.compile(r"^##\s*Vision\s*$", re.M)
NOT_A_PROMPT = ("Human action", "DECISION NEEDED", "PROMPT NEEDED")
# Directives may stand alone on their own line or be comma-joined
# ("Model: opus, Effort: max"); capture the value token (up to a comma or
# space) and let the sub strip it with any trailing separator, rather than
# anchoring to end-of-line and silently leaking a combined line.
MODEL_RE = re.compile(r"(?im)^[ \t]*Model:[ \t]*([^\s,]+)[ \t]*,?[ \t]*")
EFFORT_RE = re.compile(r"(?im)^[ \t]*Effort:[ \t]*([^\s,]+)[ \t]*,?[ \t]*")
MODELS = {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Frontmatter sanity (validate_project): the fixed status set, and the keys
# the runner and dashboard actually read. Anything else is a likely typo.
VALID_STATUSES = {"queued", "running", "review", "escalated", "done", "icebox"}
KNOWN_KEYS = {"name", "category", "status", "energy", "description", "repo",
              "updated", "autonomy", "account", "blocked_on", "fire_once"}
# A session window may write these legwork paths; anything else is audited.
TRACKER_SURFACE = ("projects/", "dashboard/")
# Transient cloud failures worth a quiet retry rather than a review cycle:
# overload, rate limiting and 5xx/connection faults from the API or harness.
TRANSIENT_RE = re.compile(
    r"overloaded|rate limit|api error|529|503|500\b|"
    r"internal server error|connection (?:error|reset)|service unavailable",
    re.I,
)
# A usage-limit cutoff is its own thing: not a per-project fault but an
# account that cannot fire productively until it resets.
USAGE_RE = re.compile(
    r"usage limit|out of (?:extra )?usage|limit reached|quota", re.I
)
# "resets 6:40pm", "resets at 18:40", "reset 6pm" -> the reset clock time.
# The trailing lookahead keeps a date ("resets 2026-06-17") from being
# misread as an 8pm clock time.
RESET_RE = re.compile(
    r"reset[s]?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?![\d:-])", re.I
)

# Sessions run parallel, but the legwork repo is shared state: every write
# section (claim, repair, dashboard rebuild, audit window) takes this lock
# and pushes before releasing it, so concurrent fires never race the index
# or sweep each other's commits. Not reentrant; never nest the sections.
WRITE_LOCK = threading.Lock()
LOG_LOCK = threading.Lock()

PREAMBLE = """Autonomous legwork session. No human is present; never wait for input.
Project file: {project_file}. Read it fully before working. {brief_line}
If you hit a decision the brief does not cover, or anything touching money,
production deploys, credentials, sending things to people, or deleting data,
do not guess: stop and wrap with status escalated and a DECISION NEEDED brief.
Work inside this repo only and commit completed work with honest messages.

{prompt}"""
VISION_BRIEF = ("Its Vision\nsection is the standing brief and stands in "
                "for the human: serve it.")
ONESHOT_BRIEF = ("The human fired\nthis single session by hand; the Next "
                 "prompt below is the whole brief.")


def log(message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_LOCK:
        with open(RUNNER_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {message}\n")


def run_git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120
    )


def rebase_in_progress(cwd):
    """True when a git rebase is half-applied in cwd (a .git/rebase-merge or
    rebase-apply directory). The tick aborts such a state instead of treating
    the conflict-marker files it leaves as ordinary tracker edits."""
    git_dir = Path(cwd) / ".git"
    return (git_dir / "rebase-merge").exists() or \
        (git_dir / "rebase-apply").exists()


def porcelain_path(line):
    """The path from a `git status --porcelain` line (cols 0-1 status, 2
    space, 3+ path). For a rename/copy ('orig -> dest') return the dest, and
    strip git's quoting so the prefix test sees the real path."""
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"')


def push_with_rebase(cwd, attempts=3):
    """Push, rebasing over whatever moved the remote first (n8n write-back,
    a parallel session's wrap). Returns True when the push landed. A failed
    rebase (a real same-line conflict on a tracker file) is aborted before
    retrying or returning, so a transient conflict can never leave the
    control-plane repo wedged mid-rebase and block every future tick."""
    for attempt in range(attempts):
        if attempt:
            if run_git(["pull", "--rebase"], cwd).returncode != 0:
                run_git(["rebase", "--abort"], cwd)
                log(f"push_with_rebase: aborted a conflicted rebase in {cwd}")
        if run_git(["push"], cwd).returncode == 0:
            return True
    return False


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def backoff_seconds(count):
    """Escalating wait after `count` consecutive transient crashes: 15 min,
    30, 60, ... capped at TRANSIENT_CAP."""
    return min(TRANSIENT_BASE * (2 ** max(0, count - 1)), TRANSIENT_CAP)


def transient_cooldown_remaining(state, name, now):
    """Seconds a project must still wait after recent transient crashes, 0
    when it is clear to fire."""
    entry = state.get("transient", {}).get(name)
    if not entry:
        return 0
    try:
        since = datetime.fromisoformat(entry["since"])
    except (ValueError, KeyError, TypeError):
        return 0
    remaining = backoff_seconds(entry.get("count", 1)) - (now - since).total_seconds()
    return max(0, int(remaining))


def usage_block_remaining(state, account, now):
    """Seconds the account is deferred for a usage limit, 0 when clear."""
    until = state.get("usage_block", {}).get(account)
    if not until:
        return 0
    try:
        return max(0, int((datetime.fromisoformat(until) - now).total_seconds()))
    except (ValueError, TypeError):
        return 0


def update_cooldowns(state, outcomes, now):
    """Fold the tick's fire outcomes into the cooldown state: a transient
    crash starts or lengthens that project's backoff, a usage limit defers
    the whole account until its reset, and any clean fire clears the
    project's transient count. Pruned of expired usage blocks so state stays
    small. Saved before returning."""
    transient = state.setdefault("transient", {})
    usage = state.setdefault("usage_block", {})
    for outcome in outcomes:
        if not outcome:
            continue
        name = outcome["name"]
        if outcome.get("limited"):
            usage[outcome["account"]] = outcome.get("reset") or (
                now + timedelta(seconds=USAGE_BLOCK_DEFAULT)
            ).isoformat(timespec="seconds")
            transient.pop(name, None)
        elif outcome.get("transient"):
            count = transient.get(name, {}).get("count", 0) + 1
            transient[name] = {"since": now.isoformat(timespec="seconds"),
                               "count": count}
        else:
            transient.pop(name, None)
    for account in list(usage):
        if usage_block_remaining(state, account, now) == 0:
            del usage[account]
    # Drop transient entries whose backoff has fully elapsed, so state does
    # not accumulate one stale entry per project that ever crashed.
    for name in list(transient):
        if transient_cooldown_remaining(state, name, now) == 0:
            del transient[name]
    save_state(state)


def send_alert(text):
    """Telegram via the alerts webhook. Fails quietly: alerting must never
    block the runner itself. A no-op when LEGWORK_ALERT_URL is unset, so the
    alerts/heartbeat are simply off when no pipeline is wired."""
    if not ALERT_URL:
        return False
    payload = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        ALERT_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=15)
        return True
    except OSError:
        return False


def extract_directives(prompt):
    """Pull optional Model: and Effort: lines out of a minted prompt.
    Returns (clean_prompt, model, effort, ignored) where ignored lists any
    unrecognised values, so a typo never blocks a fire but stays visible."""
    model = effort = None
    ignored = []
    m = MODEL_RE.search(prompt)
    if m:
        name = m.group(1).lower()
        model = MODELS.get(name)
        if model is None:
            # Not a directive we know: leave the line in the prompt (it may
            # be legitimate prose that merely starts "Model:") and only
            # report it, rather than silently eating part of the prompt.
            ignored.append(f"model={name}")
        else:
            prompt = MODEL_RE.sub("", prompt, count=1)
    e = EFFORT_RE.search(prompt)
    if e:
        name = e.group(1).lower()
        if name in EFFORTS:
            effort = name
            prompt = EFFORT_RE.sub("", prompt, count=1)
        else:
            ignored.append(f"effort={name}")
    return prompt.strip(), model, effort, ignored


def fires_today(fname):
    if not RUNNER_LOG.exists():
        return 0
    today = date.today().isoformat()
    # Match the whole "fired <fname> in " token so one project filename that
    # is a prefix of another (foo.md vs foo-bar.md) cannot over-count.
    needle = f"fired {fname} in "
    count = 0
    with open(RUNNER_LOG, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(today) and needle in line:
                count += 1
    return count


def cost_today():
    """Sum of today's per-fire costs from runner.log: the $X.XX that
    transcript_summary writes on each `completed` line. 0.0 when the log is
    absent or carries no costs today. The data behind LEGWORK_DAILY_COST_CAP."""
    if not RUNNER_LOG.exists():
        return 0.0
    today = date.today().isoformat()
    total = 0.0
    try:
        with open(RUNNER_LOG, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(today) and "  completed " in line:
                    m = COST_RE.search(line)
                    if m:
                        total += float(m.group(1))
    except OSError:
        pass
    return total


def assess(path):
    """Return (eligible, reason, details) for one project file."""
    text = path.read_text(encoding="utf-8")
    meta = parse_frontmatter(text)
    name = meta.get("name", path.stem)

    if meta.get("status", "").lower() != "queued":
        return False, f"status is {meta.get('status', 'missing')}", None
    # fire_once is the human hand-firing one session from the phone: it
    # stands in for the autonomy opt-in and the Vision section, once, and
    # nothing else. claim() consumes the key.
    fire_once = bool(meta.get("fire_once", ""))
    has_vision = bool(VISION_RE.search(text))
    if not fire_once:
        if meta.get("autonomy", "").lower() != "loop":
            return False, "no autonomy opt-in", None
        if not has_vision:
            return False, "autonomy set but no Vision section", None

    repo_value = meta.get("repo", "")
    if not repo_value or repo_value == "none":
        return False, "no repo", None
    repo_path = Path(repo_value).expanduser()
    if not (repo_path / ".git").exists():
        return False, f"repo {repo_path} is not a git repository", None

    # account: picks the Claude profile. Both an unpinned account: and an
    # account: whose CLAUDE_CONFIG_DIR_<NAME> is unset fall back to
    # CLAUDE_CONFIG_DIR -- the personal one -- so a work project can fire under
    # the personal identity with nothing in the log to say so. Refuse instead:
    # firing under the wrong account is worse than not firing.
    account = meta.get("account", "").lower()
    if meta.get("category", "").lower() == "work" and account != "work":
        return False, ("category: work needs account: work "
                       "(would fire under the personal profile)"), None
    if account and account != "personal" \
            and not os.environ.get(f"CLAUDE_CONFIG_DIR_{account.upper()}"):
        return False, (f"account '{account}' has no CLAUDE_CONFIG_DIR_"
                       f"{account.upper()} set "
                       "(would fire under the default profile)"), None

    blocked = meta.get("blocked_on", "")
    if blocked:
        return False, f"blocked_on: {blocked[:48]}", None

    match = PROMPT_RE.search(text)
    prompt = match.group(1).strip() if match else ""
    if not prompt:
        return False, "no next prompt", None
    if prompt.startswith(NOT_A_PROMPT):
        return False, f"prompt is a marker ({prompt.split('.')[0][:40]})", None
    prompt, model, effort, ignored = extract_directives(prompt)

    used = fires_today(path.name)
    if used >= DAILY_CAP:
        return False, f"daily cap reached ({used}/{DAILY_CAP})", None

    dirty = run_git(["status", "--porcelain"], repo_path)
    if dirty.returncode != 0:
        return False, f"git status failed in {repo_path}", None
    if dirty.stdout.strip():
        return False, f"target repo dirty ({repo_path})", None

    reason = "eligible"
    extras = (["fire_once"] if fire_once else []) \
        + ([f"model={model}"] if model else []) \
        + ([f"effort={effort}"] if effort else []) \
        + [f"{x} unknown, ignored" for x in ignored]
    if extras:
        reason = f"eligible ({', '.join(extras)})"

    return True, reason, {
        "file": path,
        "name": name,
        "updated": meta.get("updated", ""),
        "repo_path": repo_path,
        "prompt": prompt,
        "model": model,
        "effort": effort,
        "account": meta.get("account", "personal").lower(),
        "has_vision": has_vision,
        # The raw fire_once value, so a transient-error requeue can restore
        # the key the claim consumed.
        "fire_once": meta.get("fire_once", ""),
    }


def validate_project(path):
    """Cheap frontmatter sanity check for --dry-run and --doctor. Returns a
    list of human-readable warnings; never raises and never blocks firing, so
    a typo (a misspelled key, a bad status, an account with no config dir)
    surfaces instead of silently changing behaviour."""
    warnings = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"unreadable ({exc})"]
    meta = parse_frontmatter(text)
    if not meta:
        return ["no frontmatter found"]
    status = meta.get("status", "").lower()
    if status and status not in VALID_STATUSES:
        warnings.append(f"unknown status '{status}'")
    autonomy = meta.get("autonomy", "")
    if autonomy and autonomy.lower() != "loop":
        warnings.append(f"autonomy is '{autonomy}', only 'loop' enables the runner")
    account = meta.get("account", "")
    if account and account.lower() != "personal" \
            and not os.environ.get(f"CLAUDE_CONFIG_DIR_{account.upper()}"):
        warnings.append(f"account '{account}' has no CLAUDE_CONFIG_DIR_"
                        f"{account.upper()} set (will use the default config)")
    for key in meta:
        if key not in KNOWN_KEYS:
            warnings.append(f"unknown frontmatter key '{key}'")
    return warnings


def find_claude():
    found = shutil.which("claude")
    if found:
        return found
    for candidate in CLAUDE_FALLBACKS:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def account_config_dir(account):
    """The Claude config dir for a project's account, or "" to inherit the
    default config. A project's frontmatter account: <name> maps to the
    CLAUDE_CONFIG_DIR_<NAME> env var (uppercased); everything else falls back
    to CLAUDE_CONFIG_DIR. With neither set, sessions use the inherited config."""
    specific = os.environ.get(f"CLAUDE_CONFIG_DIR_{account.upper()}")
    return specific or DEFAULT_CONFIG_DIR


def child_env(claude_path, account):
    env = dict(os.environ)
    config_dir = account_config_dir(account)
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    if WEBHOOK_URL:
        env["LEGWORK_WEBHOOK_URL"] = WEBHOOK_URL
    env["LEGWORK_DIR"] = str(LEGWORK_DIR)
    # os.pathsep, not ":": Windows separates PATH with ";", so a ":"-joined
    # PATH is one long nonsense entry and the child session loses every tool
    # it resolves by name -- git first, which is how it commits its own work.
    # The Homebrew/local dirs only exist on macOS/Linux; on Windows the
    # inherited PATH is already the whole story.
    extra = [str(Path(claude_path).parent)]
    if os.name != "nt":
        extra += ["/usr/local/bin", "/opt/homebrew/bin"]
        inherited = env.get("PATH", "/usr/bin:/bin")
    else:
        inherited = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([*extra, inherited]).strip(os.pathsep)
    return env


def claim(project):
    """Flip queued -> running on the remote so the fire is visible. Returns
    the post-claim HEAD sha, which anchors the session-window audit, or None
    when the claim could not be published. The pull re-reads the file after
    eligibility was assessed: if n8n or a parallel wrap moved the status off
    queued in the meantime, the flip is a no-op and the claim is dropped."""
    with WRITE_LOCK:
        path = project["file"]
        run_git(["pull", "--rebase"], LEGWORK_DIR)
        text = path.read_text(encoding="utf-8")
        # Case-insensitive to match assess(): a hand-typed "status: Queued"
        # must claim (and be normalised) rather than assess eligible every
        # tick and log "claim dropped" forever.
        flipped = re.sub(r"^status:[ \t]*queued[ \t]*$", "status: running",
                         text, count=1, flags=re.M | re.I)
        if flipped == text:
            log(f"claim dropped for {path.name}: no longer queued")
            return None
        # A fire_once key is one session's consent: consume it with the
        # claim so the project cannot fire again without the human.
        flipped = re.sub(r"^fire_once:[^\n]*\n", "", flipped, count=1,
                         flags=re.M)
        write_lf(path, flipped)
        run_git(["add", str(path)], LEGWORK_DIR)
        run_git(["commit", "-m", f"legwork: runner fires {path.stem}"],
                LEGWORK_DIR)
        if push_with_rebase(LEGWORK_DIR):
            head = run_git(["rev-parse", "HEAD"], LEGWORK_DIR)
            return head.stdout.strip() or None
        # Could not publish the claim: drop it and let a later tick retry.
        # Reset to the tracked upstream (@{u}) rather than a hardcoded
        # origin/main, so this works on master or any default branch.
        run_git(["reset", "--hard", "@{u}"], LEGWORK_DIR)
        log(f"claim push failed for {path.name}, claim dropped")
        return None


def notify_reviewer(project, detail):
    # A no-op when LEGWORK_WEBHOOK_URL is unset: with no review pipeline the
    # runner's fallback post is simply skipped.
    if not WEBHOOK_URL:
        return False
    # repo carries the project file stem, not the repo folder name: the
    # review letter's first line and the reply capture's file lookup both
    # key on it, and the SessionEnd hook resolves to the same stem.
    payload = json.dumps({
        "source": "legwork-runner",
        "repo": project["file"].stem,
        "branch": "unknown",
        "last_commit": "none",
        "diff_stat": "",
        "session_commits": "",
        "uncommitted_files": "0",
        "uncommitted_list": "",
        "test_output": f"RUNNER: {detail}",
        "tracker_entry": project["file"].read_text(encoding="utf-8")[:4500],
        "session_id": "",
        "end_reason": "runner-recovery",
    }).encode("utf-8")
    request = urllib.request.Request(
        WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=15)
        return True
    except OSError:
        return False


def local_review_payload(project, pre_head, detail):
    """Assemble the same evidence shape the SessionEnd hook posts, but built
    by the runner so local review works with no hook and no webhook. The diff
    and commit list are scoped to this session via the pre-fire HEAD; the
    tracker entry carries intent; end_reason mirrors how the runner closed
    out (runner-recovery when it had to repair an unwrapped session)."""
    repo = project["repo_path"]
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
    last = run_git(["log", "-1", "--oneline"], repo).stdout.strip()
    if pre_head:
        diff = run_git(["diff", "--stat", f"{pre_head}..HEAD"], repo).stdout.strip()
        commits = run_git(["log", "--oneline", f"{pre_head}..HEAD"], repo).stdout.strip()
    else:
        diff = run_git(["diff", "--stat", "HEAD~1..HEAD"], repo).stdout.strip()
        commits = ""
    porcelain = run_git(["status", "--porcelain"], repo).stdout.splitlines()
    test_output = ""
    try:
        test_output = "\n".join((repo / ".legwork" / "last_test_output.txt")
                                .read_text(encoding="utf-8").splitlines()[-40:])
    except OSError:
        pass
    # The tracker file lives in the legwork repo, which parallel workers and a
    # pull can rewrite; read it under the same lock so the evidence cannot be
    # a torn mid-write file.
    with WRITE_LOCK:
        tracker_entry = project["file"].read_text(encoding="utf-8")[:4500]
    return {
        "repo": project["file"].stem,
        "branch": branch or "none",
        "last_commit": last or "none",
        "diff_stat": diff,
        "session_commits": commits,
        "uncommitted_files": str(len(porcelain)),
        "uncommitted_list": "\n".join(porcelain[:15]),
        "test_output": test_output,
        "tracker_entry": tracker_entry,
        "session_id": "",
        "end_reason": "runner-recovery" if detail else "normal",
    }


def apply_local_review(project, verdict):
    """Write a reviewer verdict back to the project file. Mirrors
    repair_unwrapped's shape: take WRITE_LOCK, pull so a parallel wrap is not
    clobbered, let legwork_review decide the new text, commit and push.
    Returns True when the verdict was applied, False when it was a no-op (a
    terminal status the reviewer must not resurrect)."""
    with WRITE_LOCK:
        path = project["file"]
        run_git(["pull", "--rebase"], LEGWORK_DIR)
        text = path.read_text(encoding="utf-8")
        applied = legwork_review.apply_verdict(text, verdict, datetime.now())
        if not applied:
            return False
        new_text, _new_status, _detail = applied
        write_lf(path, new_text)
        run_git(["add", str(path)], LEGWORK_DIR)
        run_git(["commit", "-m",
                 f"legwork: reviewer {verdict['verdict']} {path.stem}"],
                LEGWORK_DIR)
        if not push_with_rebase(LEGWORK_DIR):
            log(f"local review push failed for {path.name}")
        return True


def park_for_review(project, reason="local review could not produce an "
                     "actionable verdict"):
    """The reviewer call failed or returned nothing usable. Park the project
    at status review so the dashboard surfaces it for a human and the runner
    does not refire-and-retriage it in a loop. A no-op unless the project is
    still in flight (queued or running)."""
    with WRITE_LOCK:
        path = project["file"]
        run_git(["pull", "--rebase"], LEGWORK_DIR)
        text = path.read_text(encoding="utf-8")
        if parse_frontmatter(text).get("status", "").lower() \
                not in ("queued", "running"):
            return
        today = date.today().isoformat()
        text = re.sub(r"^status:[ \t]*(?:queued|running)[ \t]*$",
                      "status: review", text, count=1, flags=re.M | re.I)
        bullet = f"- {today}: Runner: {reason}; parked for human pickup.\n"
        new = re.sub(r"(##[ \t]*Log[^\n]*\n+)",
                     lambda m: m.group(1) + bullet, text, count=1)
        if new == text:  # no ## Log section to prepend under
            new = text.rstrip() + "\n\n## Log\n\n" + bullet
        text = new
        write_lf(path, text)
        run_git(["add", str(path)], LEGWORK_DIR)
        run_git(["commit", "-m",
                 f"legwork: park {path.stem} for review (reviewer call failed)"],
                LEGWORK_DIR)
        if not push_with_rebase(LEGWORK_DIR):
            log(f"park push failed for {path.name}")


def run_local_review(project, pre_head, detail):
    """The local review pipeline: build evidence, triage with a claude call,
    write the verdict back, and alert if a webhook is wired. A failed call
    parks the project for a human instead of risking a refire loop."""
    claude_path = find_claude()
    if not claude_path:
        log("local review skipped: claude binary not found")
        return
    stem = project["file"].stem
    payload = local_review_payload(project, pre_head, detail)
    verdict = legwork_review.review(payload, REVIEWER_MODEL, claude_path)
    if not verdict:
        park_for_review(project, "local review call failed (no verdict)")
        log(f"local review FAILED for {stem}: no verdict; parked for human")
        return
    if not legwork_review.is_actionable(verdict):
        # e.g. a revise with no fix prompt: writing it back would strand the
        # project, so hand it to a human instead.
        park_for_review(project, f"reviewer returned a {verdict.get('verdict')} "
                        f"with no actionable content")
        log(f"local review {verdict.get('verdict')} for {stem} not actionable; "
            f"parked for human")
        return
    applied = apply_local_review(project, verdict)
    log(f"local review {verdict['verdict']} for {stem}: "
        f"{'applied' if applied else 'skipped (terminal status)'}")
    if applied:
        send_alert(legwork_review.verdict_alert(verdict, stem))


def last_fire_line():
    """The most recent 'fired' entry in runner.log, shortened for Telegram."""
    if not RUNNER_LOG.exists():
        return ""
    last = ""
    with open(RUNNER_LOG, encoding="utf-8") as fh:
        for line in fh:
            if "  fired " in line:
                last = line.strip()
    if not last:
        return ""
    stamp = last[:16]
    name = last.split("  fired ", 1)[1].split(" in ", 1)[0]
    return f"{name} at {stamp}"


def build_heartbeat(paused=None):
    """One Telegram message a day that proves the loop is alive and surfaces
    what is silently stuck: ineligible autonomy projects, stale running
    states, waiting escalations. paused carries the pause note when a pause
    flag is set, so the heartbeat says which flag and how to lift it."""
    lines = ["Legwork heartbeat"]
    if paused:
        lines.append(f"Runner PAUSED ({paused}).")
    fired = last_fire_line()
    lines.append(f"Last fire: {fired}" if fired else "Last fire: none recorded")

    queued = escalated = 0
    stale_running = []
    auto_lines = []
    for path in sorted(PROJECTS_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            meta = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        status = meta.get("status", "").lower()
        if status == "queued":
            queued += 1
        elif status == "escalated":
            escalated += 1
        elif status == "running":
            quiet = days_since(meta.get("updated", ""))
            if quiet is not None and quiet >= 2:
                stale_running.append(f"{path.stem} ({quiet}d)")
        if meta.get("autonomy", "").lower() == "loop":
            ok, reason, _ = assess(path)
            auto_lines.append(f"  {path.stem}: {'ready' if ok else reason}")

    lines.append(f"Queued {queued}, escalated {escalated}.")
    if auto_lines:
        lines.append("Autonomy projects:")
        lines.extend(auto_lines)
    else:
        lines.append("No projects opted into autonomy.")
    if stale_running:
        lines.append(f"Running but quiet 2d+: {', '.join(stale_running)}")
    return "\n".join(lines)


def maybe_heartbeat(state, paused=None):
    today = date.today().isoformat()
    if datetime.now().hour < HEARTBEAT_HOUR or state.get("last_heartbeat") == today:
        return
    state["last_heartbeat"] = today
    save_state(state)
    sent = send_alert(build_heartbeat(paused))
    log(f"heartbeat {'sent' if sent else 'send FAILED'}")


def note_blocked(state, reason):
    """A tick could not proceed. Alert once when the blockage outlives
    STALL_ALERT_AFTER, because a silently stalled runner looks identical to
    a quiet day until something was supposed to fire."""
    now = datetime.now()
    since = state.get("blocked_since")
    if not since:
        state["blocked_since"] = now.isoformat(timespec="seconds")
        save_state(state)
        return
    try:
        started = datetime.fromisoformat(since)
    except ValueError:
        state["blocked_since"] = now.isoformat(timespec="seconds")
        save_state(state)
        return
    blocked_for = (now - started).total_seconds()
    if blocked_for >= STALL_ALERT_AFTER and not state.get("stall_alerted"):
        minutes = int(blocked_for // 60)
        sent = send_alert(
            f"Legwork runner blocked for {minutes} min: {reason}. "
            "No autonomous sessions will fire until the legwork tree is "
            "clean and pulling."
        )
        log(f"stall alert {'sent' if sent else 'send FAILED'} "
            f"({minutes} min: {reason})")
        state["stall_alerted"] = True
        save_state(state)


def clear_blocked(state):
    if "blocked_since" in state or "stall_alerted" in state:
        state.pop("blocked_since", None)
        state.pop("stall_alerted", None)
        save_state(state)
        log("tick unblocked")


def hook_fired_since(repo_name, since):
    hook_log = LEGWORK_DIR / "hook.log"
    if not hook_log.exists():
        return False
    needle = f"{repo_name}  sent:"
    for line in hook_log.read_text(encoding="utf-8").splitlines():
        if needle not in line:
            continue
        try:
            stamp = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if stamp >= since:
            return True
    return False


def session_wrapped(path, claim_head):
    """True when the claim_head..HEAD window holds a commit the session made
    itself (not a runner commit) that touched this project's tracker file:
    the /wrap landed even if the session left the status on running. Lets the
    repair tell a real crash from a wrap that just forgot the status flip,
    instead of logging 'exited without wrapping' over work that did wrap."""
    if not claim_head:
        return False
    res = run_git(["log", "--format=%s", f"{claim_head}..HEAD", "--",
                   f"projects/{path.name}"], LEGWORK_DIR)
    if res.returncode != 0:
        return False
    return any(line.strip() and not line.startswith("legwork: runner")
               for line in res.stdout.splitlines())


def repair_unwrapped(project, exit_code, minutes, claim_head=None,
                     transient=False):
    """A session left status on running: surface it instead of leaving a
    stale status. Re-queue when the crash was transient and a later tick
    should retry (bounded by DAILY_CAP); move a session that did wrap (it
    committed a tracker edit but forgot the status flip) to review without
    crying crash; otherwise flag it as exited without wrapping and move it to
    review. Returns the repair detail, or None when status was not running."""
    with WRITE_LOCK:
        path = project["file"]
        run_git(["pull", "--rebase"], LEGWORK_DIR)
        text = path.read_text(encoding="utf-8")
        meta = parse_frontmatter(text)
        if meta.get("status", "").lower() != "running":
            return None  # the session set its own status, nothing to repair
        today = date.today().isoformat()
        if transient:
            new_status = "status: queued"
            # The claim consumed a fire_once key that was one session's
            # consent, and that session never ran: restore it with the
            # re-queue, or the retry this log line promises can never fire.
            if project.get("fire_once"):
                new_status += f"\nfire_once: {project['fire_once']}"
            detail = (f"session died on a transient API error before doing "
                      f"any work (exit {exit_code} after {minutes} min); "
                      f"re-queued for retry")
            subject = f"legwork: runner re-queues {path.stem} after API error"
        elif session_wrapped(path, claim_head):
            new_status = "status: review"
            detail = (f"session wrapped but left status running; runner moved "
                      f"it to review (exit {exit_code} after {minutes} min)")
            subject = f"legwork: runner moves wrapped {path.stem} to review"
        else:
            new_status = "status: review"
            detail = (f"autonomous session exited without wrapping "
                      f"(exit {exit_code} after {minutes} min)")
            subject = f"legwork: runner flags unwrapped session on {path.stem}"
        text = re.sub(r"^status:[ \t]*running[ \t]*$", lambda m: new_status,
                      text, count=1, flags=re.M | re.I)
        bullet = (f"- {today}: Runner: {detail}. "
                  f"Transcript in .runner-logs/.\n")
        new = re.sub(r"(##[ \t]*Log[^\n]*\n+)",
                     lambda m: m.group(1) + bullet, text, count=1)
        if new == text:  # no ## Log section to prepend under
            new = text.rstrip() + "\n\n## Log\n\n" + bullet
        text = new
        write_lf(path, text)
        run_git(["add", str(path)], LEGWORK_DIR)
        run_git(["commit", "-m", subject], LEGWORK_DIR)
        if not push_with_rebase(LEGWORK_DIR):
            log(f"repair push failed for {path.name}")
        return detail


def transcript_result(transcript):
    """The stream-json transcript ends with a result object carrying cost,
    turn count and the final text. Returns it, or None."""
    try:
        lines = transcript.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "result":
            return obj
    return None


def transcript_summary(transcript):
    """Short cost/turns suffix for the completed log line."""
    obj = transcript_result(transcript)
    if not obj:
        return ""
    parts = []
    cost = obj.get("total_cost_usd")
    if isinstance(cost, (int, float)):
        parts.append(f"${cost:.2f}")
    turns = obj.get("num_turns")
    if turns:
        parts.append(f"{turns} turns")
    if obj.get("is_error"):
        parts.append("is_error")
    return f", {', '.join(parts)}" if parts else ""


def did_work(result_obj):
    """True when the session got past its first turn or spent anything.
    Zero turns and zero cost mean nothing was attempted, so the crash can be
    retried without losing work; anything else has output worth a review."""
    if not result_obj:
        return False
    return bool(result_obj.get("num_turns", 0) > 1
                or (result_obj.get("total_cost_usd") or 0))


def is_transient_crash(result_obj):
    """True when the session died on its very first API call with an
    error that retrying plainly fixes (529 overloaded, rate limit, 5xx,
    connection faults). Zero work attempted means a quiet re-queue beats a
    review cycle and a Telegram letter. Anything that did real work before
    failing is a genuine failure and goes to review. Usage limits are not
    transient in this sense; usage_limit_reset handles them."""
    if not result_obj or not result_obj.get("is_error"):
        return False
    if did_work(result_obj):
        return False
    text = str(result_obj.get("result", ""))
    if USAGE_RE.search(text):
        return False
    return bool(TRANSIENT_RE.search(text))


def usage_limit_reset(result_obj, now):
    """Classify a usage-limit cutoff. Returns (limited, reset_iso):
    (False, None) when the session was not usage-limited; (True, None) when
    it was but named no reset clock; (True, iso) when a reset time was read,
    rolled to tomorrow if it has already passed today. The account, not the
    project, is what a usage limit blocks, so this is independent of whether
    the session did any work."""
    if not result_obj or not result_obj.get("is_error"):
        return False, None
    text = str(result_obj.get("result", ""))
    if not USAGE_RE.search(text):
        return False, None
    m = RESET_RE.search(text)
    if not m:
        return True, None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return True, None
    reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset <= now:
        reset += timedelta(days=1)
    return True, reset.isoformat(timespec="seconds")


def classify_outcome(result_obj, now):
    """Classify a finished session's result object into the booleans the
    runner acts on. Pure (no side effects), so the fire() decision tail is
    unit-testable against transcript fixtures. requeue means nothing
    salvageable ran, so a later tick should retry quietly rather than open a
    review: a transient crash, or a usage limit hit before any work."""
    limited, reset_iso = usage_limit_reset(result_obj, now)
    transient = (not limited) and is_transient_crash(result_obj)
    requeue = transient or (limited and not did_work(result_obj))
    return {"transient": transient, "limited": limited,
            "reset": reset_iso, "requeue": requeue}


def audit_session_window(project, claim_head):
    """Everything a session window wrote to the legwork repo outside the
    tracker surface (projects/, dashboard/) gets a Telegram alert. The
    legwork repo is the control plane: a worker session quietly editing the
    runner, hooks or reviewer is exactly the failure this catches. Human
    commits in the window can trip it too; the alert says to ignore those."""
    with WRITE_LOCK:
        names = run_git(["diff", "--name-only", f"{claim_head}..HEAD"],
                        LEGWORK_DIR)
        if names.returncode != 0:
            return
        offending = [f for f in names.stdout.splitlines()
                     if f.strip() and not f.startswith(TRACKER_SURFACE)]
        if not offending:
            return
        commits = run_git(["log", "--oneline", f"{claim_head}..HEAD"],
                          LEGWORK_DIR).stdout.strip()
    detail = (f"legwork repo touched outside projects/ and dashboard/ "
              f"during the {project['file'].stem} session window: "
              f"{', '.join(offending[:6])}")
    log(f"AUDIT: {detail}")
    send_alert(
        f"Legwork audit: {detail}\n\nCommits in the window:\n{commits[:600]}\n\n"
        "If these commits are yours, ignore this. If not, read them now."
    )


def write_guard_settings():
    """Write the per-session Claude settings file the runner passes with
    --settings: deny rules that block a fired session from editing the legwork
    control plane (the runner, hooks, reviewer and n8n pipelines). A session
    may still freely edit the target repo, projects/ and dashboard/ to do its
    work and /wrap. Deny rules win over acceptEdits, so this holds even with no
    review webhook wired. The post-hoc audit_session_window() stays as a second
    layer. Best-effort: a write failure just falls back to that audit."""
    # Claude Code permission rules read a single leading "/" as relative to
    # the project root, so a POSIX absolute path must be prefixed with "//"
    # or the deny silently matches nothing (verified by firing a real session).
    # A Windows path is drive-absolute ("E:/...") and already unambiguous, so
    # there the prefix must be DROPPED: the "//E:/..." spelling matches
    # nothing and the guard is void. Verified live on Windows against a
    # no-deny control that proved the probe could write: only the prefix-less
    # spellings blocked it.
    if os.name == "nt":
        base, prefix = LEGWORK_DIR.as_posix(), ""
    else:
        base, prefix = str(LEGWORK_DIR).lstrip("/"), "//"
    deny = []
    for sub in ("core", "suite", "scripts"):
        path = f"{prefix}{base}/{sub}/**"
        for tool in ("Edit", "Write", "MultiEdit"):
            deny.append(f"{tool}({path})")
    try:
        GUARD_SETTINGS.write_text(
            json.dumps({"permissions": {"deny": deny}}), encoding="utf-8")
    except OSError:
        pass


def fire(project):
    claude_path = find_claude()
    if not claude_path:
        log("ERROR: claude binary not found, cannot fire")
        return

    claim_head = claim(project)
    if not claim_head:
        return
    started = datetime.now()
    try:
        return fire_claimed(project, claude_path, claim_head, started)
    except Exception:
        # The claim just published status: running. A crash anywhere in the
        # post-claim work (a failed mkdir or Popen, a hung push) must not
        # strand that status until the daily heartbeat notices days later:
        # repair it in front of a human, then re-raise to fire_thread's net.
        minutes = max(1, int((datetime.now() - started).total_seconds() // 60))
        try:
            repair_unwrapped(project, "runner crash", minutes,
                             claim_head=claim_head)
        except Exception as repair_exc:  # noqa: BLE001 - keep the original error
            log(f"ERROR: post-crash repair of {project['file'].name} "
                f"failed too: {repair_exc}")
        raise


def build_fire_argv(claude_path, prompt, project, guard):
    """The argv for one fired session. Pure: no side effects, no spawning, so
    what a session is permitted to do can be pinned by a test directly rather
    than inferred from whatever a shim on PATH managed to capture.

    (It has to be inferrable that way: a shim can only be a .cmd on Windows,
    which routes through cmd.exe and truncates the multi-line -p prompt at its
    first newline. The real claude.exe is spawned by CreateProcess with no
    shell in between, which carries the prompt intact -- verified live.)"""
    argv = [
        claude_path, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "acceptEdits",
        "--add-dir", str(LEGWORK_DIR),
    ]
    if guard:
        # Deny the edit tools on the legwork control plane (core/, suite/
        # and scripts/). Bash(git:*) is not covered, so this blocks the
        # direct path only; the post-fire audit is the detector.
        argv += ["--settings", guard]
    argv += ["--allowedTools", "Bash(git:*)", "Bash(mkdir:*)"]
    # Allow every spelling of the interpreter a session might reasonably type
    # to rebuild the dashboard. The skill says `python3`, which is right on
    # macOS/Linux but is the 0-byte Store stub on Windows, where a session
    # must use `python`. An allowlist entry that matches nothing the session
    # actually runs fails silently -- the rebuild is just refused -- so list
    # both names plus this runner's own absolute interpreter.
    builder = "core/build_dashboard.py"
    for exe in dict.fromkeys(["python3", "python", python_exe()]):
        for target in (builder, f"{LEGWORK_DIR}/{builder}"):
            argv.append(f"Bash({exe} {target}:*)")
    if project["model"]:
        argv += ["--model", project["model"]]
    if project["effort"]:
        argv += ["--effort", project["effort"]]
    return argv


def fire_claimed(project, claude_path, claim_head, started):
    """The post-claim work: run the session, classify the outcome, repair,
    review and audit. Split out of fire() so a crash anywhere in here still
    repairs the running status the claim just published."""
    # The target repo's HEAD before the session, so local review can diff
    # exactly what this session committed without depending on the hook. Guard
    # on the return code: on a commitless repo `git rev-parse HEAD` exits non-
    # zero but still echoes the literal "HEAD", which would poison the diff.
    head_res = run_git(["rev-parse", "HEAD"], project["repo_path"])
    pre_head = head_res.stdout.strip() if head_res.returncode == 0 else None

    TRANSCRIPTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    transcript = TRANSCRIPTS / f"{stamp}-{project['file'].stem}.jsonl"
    prompt = PREAMBLE.format(
        project_file=str(project["file"]),
        brief_line=VISION_BRIEF if project.get("has_vision") else ONESHOT_BRIEF,
        prompt=project["prompt"],
    )

    chosen = "".join(
        f" {k}={v}" for k, v in
        (("model", project["model"]), ("effort", project["effort"])) if v
    )
    log(f"fired {project['file'].name} in {project['repo_path']}"
        f"{chosen} (transcript {transcript.name})")
    guard = str(GUARD_SETTINGS) if GUARD_SETTINGS.exists() else None
    argv = build_fire_argv(claude_path, prompt, project, guard)
    with open(transcript, "w", encoding="utf-8") as out:
        proc = subprocess.Popen(
            argv,
            cwd=project["repo_path"], env=child_env(claude_path, project["account"]),
            stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        try:
            exit_code = proc.wait(timeout=SESSION_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                exit_code = proc.wait(timeout=GRACE)
            except subprocess.TimeoutExpired:
                proc.kill()
                exit_code = proc.wait()
            log(f"timeout: {project['file'].name} terminated after "
                f"{SESSION_TIMEOUT // 60} min")

    minutes = max(1, int((datetime.now() - started).total_seconds() // 60))
    log(f"completed {project['file'].name}: exit {exit_code}, {minutes} min"
        f"{transcript_summary(transcript)}")
    result_obj = transcript_result(transcript)
    # A zero-work crash (transient or a usage limit before anything ran) is
    # re-queued quietly; a usage limit that hit mid-work still has output to
    # review. The account-level usage block, set by the caller, is what
    # actually paces firing through the reset.
    outcome = classify_outcome(result_obj, datetime.now())
    limited, reset_iso = outcome["limited"], outcome["reset"]
    transient, requeue = outcome["transient"], outcome["requeue"]
    detail = repair_unwrapped(project, exit_code, minutes,
                              claim_head=claim_head, transient=requeue)
    if limited:
        log(f"usage limit hit on {project['file'].name} "
            f"(account {project['account']}, reset {reset_iso or 'unknown'}); "
            f"account deferred")
    if detail and requeue:
        # No salvageable work and the project is queued again: a later tick
        # retries quietly, so the reviewer has nothing to look at.
        log(f"zero-work crash, re-queued {project['file'].name}")
    elif WEBHOOK_URL:
        time.sleep(HOOK_GRACE)
        # The hook logs the resolved project stem, not the folder name.
        if not hook_fired_since(project["file"].stem, started):
            reason = detail or (
                f"session wrapped but no SessionEnd hook fired "
                f"(account {project['account']}, exit {exit_code}, {minutes} min)"
            )
            sent = notify_reviewer(project, reason)
            log(f"reviewer notified directly for {project['file'].name}: "
                f"{'ok' if sent else 'FAILED'}")
    elif LOCAL_REVIEW:
        # No n8n webhook, but local review is opted in: triage this session
        # in-process with a claude call and write the verdict straight back.
        run_local_review(project, pre_head, detail)
    rebuild_dashboard()
    audit_session_window(project, claim_head)
    return {
        "name": project["file"].stem,
        "account": project["account"],
        "transient": transient,
        "limited": limited,
        "reset": reset_iso,
    }


def fire_thread(project):
    """fire() with a crash net: one project blowing up must not take the
    other in-flight sessions down with it. Returns fire()'s outcome dict, or
    None when the session never fired or the worker crashed."""
    try:
        return fire(project)
    except Exception as exc:  # noqa: BLE001 - the net is the point
        log(f"ERROR: fire {project['file'].name} crashed: {exc}")
        return None


def fire_all(projects):
    """One worker thread per project: target-repo sessions run fully
    parallel, legwork-repo writes serialise behind WRITE_LOCK. Returns the
    list of fire outcomes so the caller can update cooldown state."""
    if len(projects) == 1:
        return [fire_thread(projects[0])]
    outcomes = [None] * len(projects)

    def run(index, project):
        outcomes[index] = fire_thread(project)

    threads = [
        threading.Thread(target=run, args=(i, p), name=p["file"].stem)
        for i, p in enumerate(projects)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return outcomes


def rebuild_dashboard():
    """Safety net: the session's own wrap may lack permission to rebuild."""
    with WRITE_LOCK:
        subprocess.run(
            [sys.executable, str(LEGWORK_DIR / "core" / "build_dashboard.py")],
            cwd=LEGWORK_DIR, capture_output=True, timeout=60,
        )
        changed = run_git(["status", "--porcelain", "dashboard"], LEGWORK_DIR)
        if changed.stdout.strip():
            run_git(["add", "dashboard"], LEGWORK_DIR)
            run_git(["commit", "-m", "legwork: runner rebuilds dashboard"],
                    LEGWORK_DIR)
            if not push_with_rebase(LEGWORK_DIR):
                log("dashboard rebuild push failed")


def pid_state(pid):
    """Whether the lock holder is still running: "dead", "alive", or "denied"
    (alive, but owned by another user, so we must not reclaim its lock).

    Never os.kill(pid, 0) on Windows. os.kill() there ignores the signal
    number for everything but CTRL_C/CTRL_BREAK_EVENT and calls
    TerminateProcess, so the liveness CHECK would kill the live runner it is
    asking about -- and a dead pid raises a plain OSError rather than
    ProcessLookupError, so the stale-lock path would escape uncaught and take
    the runner down. Verified on Windows 11 26200."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return "alive"
        except ProcessLookupError:
            return "dead"
        except PermissionError:
            return "denied"
        except OSError:
            return "dead"

    import ctypes
    from ctypes import wintypes

    ERROR_ACCESS_DENIED = 5
    STILL_ACTIVE = 259
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied means the pid exists but belongs to someone else;
        # anything else (invalid parameter) means there is no such process.
        return ("denied" if ctypes.get_last_error() == ERROR_ACCESS_DENIED
                else "dead")
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return "alive"  # cannot tell; treat as alive and never reclaim
        return "alive" if code.value == STILL_ACTIVE else "dead"
    finally:
        kernel32.CloseHandle(handle)


def acquire_lock():
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {time.time()}".encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            parts = LOCK_FILE.read_text().split()
            pid = int(parts[0])
            held = time.time() - float(parts[1]) if len(parts) > 1 else 0
        except (ValueError, OSError, IndexError):
            LOCK_FILE.unlink(missing_ok=True)  # unreadable/garbage lock
            return acquire_lock()
        state = pid_state(pid)
        if state == "dead":
            LOCK_FILE.unlink(missing_ok=True)  # stale lock from a dead run
            return acquire_lock()
        if state == "denied":
            return False  # alive and owned by another user
        # Holder is alive. Within the time budget this is a normal long
        # session; past LOCK_MAX_AGE the runner itself is wedged, so reclaim
        # the lock and alert once rather than let the queue stall silently.
        if held > LOCK_MAX_AGE:
            send_alert(f"Legwork runner lock held by live PID {pid} for "
                       f"{int(held // 60)} min — likely wedged; reclaiming.")
            log(f"reclaimed stale live-PID lock (pid {pid}, "
                f"held {int(held // 60)} min)")
            LOCK_FILE.unlink(missing_ok=True)
            return acquire_lock()
        return False  # holder is alive and within the time budget


def release_lock():
    """Delete the lock only when this process still owns it. A later run may
    have reclaimed a wedged lock (LOCK_MAX_AGE) and written its own; blindly
    unlinking here would strip that run's protection mid-flight."""
    try:
        if LOCK_FILE.read_text().split()[0] == str(os.getpid()):
            LOCK_FILE.unlink()
    except (OSError, IndexError, ValueError):
        pass


def tick(dry_run=False):
    for warning in CONFIG_WARNINGS:
        print(f"config: {warning}") if dry_run else log(f"CONFIG: {warning}")
    pause_note = None
    if PAUSE_FILE.exists():
        pause_note = ".runner-pause exists; delete it to resume"
    elif REMOTE_PAUSE_FILE.exists():
        pause_note = ".runner-pause-remote is committed; send /resume in Telegram"
    if pause_note:
        if dry_run:
            print(f"Paused: {pause_note}.")
        else:
            # Pausing is deliberate, but forgetting the pause is not: the
            # daily heartbeat still goes out and says so.
            maybe_heartbeat(load_state(), paused=pause_note)
        return

    if not dry_run:
        state = load_state()
        blocked = None
        dirty = run_git(["status", "--porcelain"], LEGWORK_DIR)
        if dirty.returncode != 0:
            blocked = "git status failed in the legwork repo"
        elif rebase_in_progress(LEGWORK_DIR):
            # A half-applied rebase (from an interrupted pull) leaves conflict
            # markers the auto-commit branch would otherwise sweep into a
            # commit. Abort it and retry on a clean tree next tick.
            run_git(["rebase", "--abort"], LEGWORK_DIR)
            log("aborted an interrupted rebase in the legwork repo")
            blocked = "recovered from an interrupted rebase; retrying next tick"
        else:
            # Untracked files are safe to tick over: the pull rebases past
            # them and every runner commit is path-scoped. Uncommitted
            # tracked changes block the pull and could leak into commits.
            # The one exception is a tracker-only edit (everything under
            # projects/): a manual work-account wrap leaves projects/<x>.md
            # dirty with no hook to commit it, which would stall firing
            # forever. The runner commits those itself and proceeds. Any
            # dirty path outside projects/ still blocks until a human acts.
            tracked = [line for line in dirty.stdout.splitlines()
                       if line.strip() and not line.startswith("??")]
            if tracked and all(porcelain_path(line).startswith("projects/")
                               for line in tracked):
                # -u stages only tracked files: an untracked scratch file
                # sitting in projects/ must not be swept into the commit.
                run_git(["add", "-u", "projects"], LEGWORK_DIR)
                committed = run_git(
                    ["commit", "-m",
                     "legwork: runner auto-commits tracker edits"],
                    LEGWORK_DIR)
                if committed.returncode == 0:
                    push_with_rebase(LEGWORK_DIR)
                    names = ", ".join(sorted(
                        {porcelain_path(line) for line in tracked}))
                    log(f"auto-committed tracker edits ({len(tracked)} "
                        f"files): {names}")
                    tracked = []
            if tracked:
                outside = sorted({porcelain_path(line) for line in tracked
                                  if not porcelain_path(line).startswith("projects/")})
                blocked = (f"uncommitted tracked changes in the legwork "
                           f"repo ({len(tracked)} files"
                           + (f": {', '.join(outside)}" if outside else "")
                           + ")")
        if blocked is None:
            pull = run_git(["pull", "--rebase"], LEGWORK_DIR)
            if pull.returncode != 0:
                # Abort any half-applied rebase so the tree is clean for the
                # next tick instead of wedged with conflict markers.
                run_git(["rebase", "--abort"], LEGWORK_DIR)
                blocked = f"pull failed ({pull.stderr.strip()[:120]})"
        if blocked:
            log(f"skipped tick: {blocked}")
            note_blocked(state, blocked)
            # The daily pulse still goes out: a long blockage must not also
            # silence the heartbeat, or the stall is invisible end to end.
            maybe_heartbeat(state)
            return
        clear_blocked(state)
        if REMOTE_PAUSE_FILE.exists():
            # The pull may have just delivered a /pause commit; honor it on
            # this tick instead of firing one last window.
            maybe_heartbeat(state, paused=".runner-pause-remote is committed; "
                                          "send /resume in Telegram")
            return
        maybe_heartbeat(state)

    eligible = []
    for path in sorted(PROJECTS_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        ok, reason, details = assess(path)
        if dry_run:
            print(f"{path.name:28} {'FIRE' if ok else 'skip'}  {reason}")
            for warning in validate_project(path):
                print(f"{'':28} !  {warning}")
        if ok:
            eligible.append(details)

    if not eligible:
        return

    # Defer projects still cooling down from a transient crash, and whole
    # accounts deferred by a recent usage limit, so a 529 storm or a hit
    # quota is not fired into every five minutes. Backoff and usage state
    # both live in .runner-state.json; dry-run reads it without changing it.
    cooldown_state = state if not dry_run else load_state()
    now = datetime.now()
    ready = []
    for project in eligible:
        blocked_for = usage_block_remaining(cooldown_state, project["account"], now)
        if blocked_for:
            note = (f"{project['file'].name} deferred: {project['account']} "
                    f"account usage-limited, ~{blocked_for // 60} min left")
            print(f"deferred: {note}") if dry_run else log(f"deferred: {note}")
            continue
        cooling = transient_cooldown_remaining(cooldown_state, project["file"].stem, now)
        if cooling:
            note = (f"{project['file'].name} deferred: transient-crash backoff, "
                    f"~{cooling // 60} min left")
            print(f"deferred: {note}") if dry_run else log(f"deferred: {note}")
            continue
        ready.append(project)
    eligible = ready
    if not eligible:
        return

    # Optional spend guard: once today's cost crosses the cap, fire nothing
    # more today. Alerts once a day, like the stall path, so the cap is
    # visible rather than a silent stop.
    if DAILY_COST_CAP:
        spent = cost_today()
        if spent >= DAILY_COST_CAP:
            note = (f"daily cost cap reached "
                    f"(${spent:.2f} >= ${DAILY_COST_CAP:.2f})")
            if dry_run:
                print(f"Cost cap: {note}; firing would be skipped")
            else:
                today = date.today().isoformat()
                if state.get("cost_capped_date") != today:
                    state["cost_capped_date"] = today
                    save_state(state)
                    send_alert(f"Legwork {note}; no more fires today.")
                log(f"skipped firing: {note}")
                return

    # Every eligible project fires, one session each. Oldest updated first
    # decides who wins when two projects point at the same repo: sessions
    # sharing a working tree would collide, so only the oldest fires and
    # the other waits for a later tick.
    eligible.sort(key=lambda p: p["updated"])
    seen_repos = set()
    firing = []
    for project in eligible:
        repo_key = str(project["repo_path"].resolve())
        if repo_key in seen_repos:
            note = (f"{project['file'].name} shares {repo_key} with an "
                    f"already-firing project, deferred to a later tick")
            if dry_run:
                print(f"deferred: {note}")
            else:
                log(f"deferred: {note}")
            continue
        seen_repos.add(repo_key)
        firing.append(project)

    if dry_run:
        names = ", ".join(p["file"].name for p in firing)
        print(f"\nWould fire: {names}")
        return
    write_guard_settings()
    outcomes = fire_all(firing)
    update_cooldowns(state, outcomes, datetime.now())


def doctor():
    """Preflight checklist printed to stdout; changes nothing. One line per
    check so a misconfigured install (no claude binary, a non-git legwork
    dir, a project with a bad status) is caught before launchd fires into it.
    Optional pieces report their state without counting as failures."""
    problems = [0]

    def check(label, good, detail=""):
        if not good:
            problems[0] += 1
        print(f"[{'OK  ' if good else 'FAIL'}] {label}"
              + (f": {detail}" if detail else ""))

    claude = find_claude()
    check("claude binary on PATH", bool(claude), claude or "not found")
    check("LEGWORK_DIR exists", LEGWORK_DIR.is_dir(), str(LEGWORK_DIR))
    check("legwork repo is a git repo", (LEGWORK_DIR / ".git").exists())
    check("projects/ exists", PROJECTS_DIR.is_dir(), str(PROJECTS_DIR))
    check("dashboard builder present",
          (LEGWORK_DIR / "core" / "build_dashboard.py").is_file())
    if (LEGWORK_DIR / ".git").exists():
        check("legwork git status readable",
              run_git(["status", "--porcelain"], LEGWORK_DIR).returncode == 0)
    check("numeric config values parse", not CONFIG_WARNINGS,
          "; ".join(CONFIG_WARNINGS))
    if WEBHOOK_URL:
        review_mode = "n8n webhook"
    elif LOCAL_REVIEW:
        review_mode = f"local ({REVIEWER_MODEL})"
    else:
        review_mode = "off (fire-and-wrap only)"
    print(f"[info] review:        {review_mode}")
    print(f"[info] alert webhook:  "
          f"{'configured' if ALERT_URL else 'unset (alerts/heartbeat off)'}")
    print(f"[info] default config: "
          f"{DEFAULT_CONFIG_DIR or 'unset (inherits the interactive account)'}")
    print(f"[info] daily cost cap: "
          f"{('$%.2f' % DAILY_COST_CAP) if DAILY_COST_CAP else 'none'}")
    warnings = []
    if PROJECTS_DIR.is_dir():
        for path in sorted(PROJECTS_DIR.glob("*.md")):
            if path.name.startswith("_"):
                continue
            for warning in validate_project(path):
                warnings.append(f"{path.name}: {warning}")
    if warnings:
        print("\nProject warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    print("\nDoctor:",
          "all green" if not problems[0] else f"{problems[0]} problem(s) found")
    return problems[0] == 0


def main():
    if "--doctor" in sys.argv:
        doctor()
        return
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        tick(dry_run=True)
        return
    if not acquire_lock():
        return
    try:
        tick()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
