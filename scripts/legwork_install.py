#!/usr/bin/env python3
"""Interactive installer for legwork, the autonomous project queue.

Zero dependencies, like everything in scripts/. Run it from the repo root,
normally through the wrapper:

    ./install.sh                 the visual wizard
    python3 scripts/legwork_install.py        same thing, directly
    python3 scripts/legwork_install.py --yes  accept every default, no prompts
    python3 scripts/legwork_install.py --no-color   plain output

A non-interactive --yes run never touches anything outside the repo: it
writes config and creates the repo dirs, but skips the user-level command
install, the launchd/cron timer and the Claude hooks unless you opt in with
--with-commands / --with-launchd / --with-hooks.

It walks one screen at a time: it asks for every value legwork can be
configured with (the legwork dir, the daily fire and cost caps, the review
mode and reviewer model, an optional dedicated Claude config dir, the tick
interval), shows you the config it will write, then offers to activate the
pieces that live OUTSIDE the repo, asking before each one:

  - write `config` and create `projects/` and `.runner-logs/` in the repo
  - copy the slash commands (/add, /wrap, /pickup, /vision, /log, /shelve)
    and the legwork-tracker skill into user-level `~/.claude`, so the manual
    loop works from any repo, not just this checkout
  - install and load the launchd agent (macOS) or a crontab line (Linux)
  - register the SessionStart/SessionEnd hooks in your Claude `settings.json`

The only thing it does not do is fill your queue: add projects with the
`/add` skill and grant autonomy with `/vision`, per project.

The functions that build file contents (render_config, render_plist,
cron_schedule, render_crontab_line, merge_hooks, parse_config_text and the
validators) are pure and side-effect free, so importing this module does
nothing and the test suite can exercise them directly. Nothing below the
helpers runs until main() is called under __main__.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from legwork_common import iter_config_pairs

REPO = Path(__file__).resolve().parent.parent
PLIST_TEMPLATE = REPO / "scripts" / "com.legwork.runner.plist"
START_HOOK = "session_start_hook.sh"
END_HOOK = "session_end_hook.sh"
PLIST_NAME = "com.legwork.runner.plist"
DEFAULT_REVIEWER_MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Pure builders (no I/O; covered by the test suite)
# ---------------------------------------------------------------------------

def cron_schedule(minutes):
    """A crontab schedule expression for a tick every `minutes` minutes.

    Sub-hour intervals become a step on the minute field; whole-hour
    intervals step the hour field, so a 60-minute tick reads `0 * * * *`
    rather than the invalid `*/60`. Daily or longer collapses to midnight."""
    minutes = int(minutes)
    if minutes < 1:
        minutes = 1
    if minutes < 60:
        return f"*/{minutes} * * * *"
    if minutes % 60 == 0:
        hours = minutes // 60
        if hours < 24:
            return f"0 */{hours} * * *"
        return "0 0 * * *"
    # An odd multi-hour value: fall back to the nearest whole hour.
    return f"0 */{max(1, round(minutes / 60))} * * *"


def render_plist(template, legwork_dir, python_bin, interval_seconds):
    """Fill the launchd template: the two `__PLACEHOLDERS__` plus the
    StartInterval value. The template stays a valid, hand-editable file (its
    sed recipe still works); we additionally retarget the interval so the
    installed agent ticks at the chosen cadence."""
    text = template.replace("__LEGWORK_DIR__", str(legwork_dir))
    text = text.replace("__PYTHON__", str(python_bin))
    text = re.sub(
        r"(<key>StartInterval</key>\s*<integer>)\d+(</integer>)",
        lambda m: f"{m.group(1)}{int(interval_seconds)}{m.group(2)}",
        text,
    )
    return text


def render_crontab_line(legwork_dir, python_bin, schedule):
    """The crontab line that ticks the runner, tagged with a marker comment
    so the installer can find and replace its own line idempotently."""
    runner = Path(legwork_dir) / "scripts" / "legwork_runner.py"
    log = Path(legwork_dir) / ".runner-logs" / "cron.log"
    return (f"{schedule} {python_bin} {runner} "
            f">> {log} 2>&1  {CRON_MARKER}")


CRON_MARKER = "# legwork runner"


def render_config(v):
    """Render a `config` file from a values dict. Only the lines that matter
    for the chosen review mode are left active; the rest stay as commented
    guidance, so the file documents itself and mirrors config.example."""
    out = []
    w = out.append
    w("# Legwork configuration. Written by scripts/legwork_install.py.")
    w("#")
    w("# This file is gitignored. scripts/legwork_runner.py loads it at")
    w("# startup, so launchd, cron and manual runs share one source of truth.")
    w("# Real environment variables override anything set here. Values may use")
    w("# $HOME and ~, which are expanded. Lines are KEY=VALUE; # is a comment.")
    w("")
    w("# Where the legwork repo lives: projects/, runner.log, the dashboard")
    w("# and runner state all sit under here.")
    w(f"LEGWORK_DIR={v['legwork_dir']}")
    w("")
    w("# Autonomous fires per project per calendar day.")
    w(f"LEGWORK_DAILY_CAP={v['daily_cap']}")
    w("")
    w("# Optional spend guard, in dollars. 0 means no cost cap.")
    cost = v.get("daily_cost_cap", 0)
    if cost and float(cost) > 0:
        w(f"LEGWORK_DAILY_COST_CAP={_trim_num(cost)}")
    else:
        w("# LEGWORK_DAILY_COST_CAP=10")
    w("")

    mode = v.get("review_mode", "off")
    w("# --- Review pipeline ---------------------------------------------------")
    if mode == "n8n":
        w("# n8n review pipeline: the runner and the SessionEnd hook POST")
        w("# session evidence here for review, and alerts/heartbeat go to the")
        w("# alert URL. See SETUP.md step 6 to import the workflows.")
        w(f"LEGWORK_WEBHOOK_URL={v.get('webhook_url', '')}")
        if v.get("alert_url"):
            w(f"LEGWORK_ALERT_URL={v['alert_url']}")
        else:
            w("# LEGWORK_ALERT_URL=https://your-n8n-host/webhook/legwork-alert")
    elif mode == "local":
        w("# Local reviewer: no n8n. The runner triages each finished session")
        w("# in-process with a `claude -p` call and writes the verdict back to")
        w("# the project file. See scripts/legwork_review.py.")
        w("LEGWORK_LOCAL_REVIEW=1")
    else:
        w("# No reviewer: the runner still fires sessions and they still wrap;")
        w("# the review post and the Telegram alerts are simply skipped.")
        w("# Set LEGWORK_LOCAL_REVIEW=1 for the no-n8n reviewer, or point")
        w("# LEGWORK_WEBHOOK_URL at an n8n instance. See SETUP.md.")
    w("")

    model = v.get("reviewer_model") or DEFAULT_REVIEWER_MODEL
    w("# The model the reviewer call uses (local reviewer and n8n build node).")
    if mode in ("local", "n8n") and model != DEFAULT_REVIEWER_MODEL:
        w(f"REVIEWER_MODEL={model}")
    else:
        w(f"# REVIEWER_MODEL={model}")
    w("")

    w("# --- Claude config dir -------------------------------------------------")
    w("# Run autonomous sessions under a dedicated Claude config dir so they")
    w("# never inherit whatever account your interactive shell defaults to.")
    if v.get("claude_config_dir"):
        w(f"CLAUDE_CONFIG_DIR={v['claude_config_dir']}")
    else:
        w("# CLAUDE_CONFIG_DIR=$HOME/.claude-legwork")
    w("# CLAUDE_CONFIG_DIR_WORK=$HOME/.claude-legwork-work")
    w("")
    return "\n".join(out) + "\n"


def _trim_num(value):
    """Render a number without a trailing `.0`, so 10.0 prints as 10."""
    f = float(value)
    return str(int(f)) if f == int(f) else str(f)


def parse_config_text(text):
    """Parse an existing `config` into a dict, the same KEY=VALUE rules the
    runner's load_config uses, so a re-run can pre-fill its prompts with what
    is already there. Quotes are stripped; $VARS and ~ are NOT expanded here,
    so the prompt shows the file's own text."""
    return dict(iter_config_pairs(text))


def plan_verb_installs(repo_claude_dir, dest_base):
    """(source, destination) pairs that install the interactive verbs
    user-level: every `.claude/commands/*.md` plus the whole legwork-tracker
    skill, mirrored under `<dest_base>/commands` and `<dest_base>/skills`.
    Read-only: computes the copy plan, copies nothing."""
    repo_claude_dir = Path(repo_claude_dir)
    dest_base = Path(dest_base)
    pairs = []
    for src in sorted((repo_claude_dir / "commands").glob("*.md")):
        pairs.append((src, dest_base / "commands" / src.name))
    skills = repo_claude_dir / "skills"
    skill = skills / "legwork-tracker"
    for src in sorted(p for p in skill.rglob("*") if p.is_file()):
        pairs.append((src, dest_base / "skills" / src.relative_to(skills)))
    return pairs


def merge_hooks(settings, legwork_dir):
    """Return a copy of a Claude settings dict with the legwork SessionStart
    and SessionEnd hooks registered. Idempotent: a command already pointing
    at our hook script for that event is left alone, so re-running the
    installer never duplicates an entry or disturbs unrelated hooks."""
    settings = dict(settings) if settings else {}
    hooks = dict(settings.get("hooks") or {})
    for event, script in ((("SessionStart"), START_HOOK),
                          (("SessionEnd"), END_HOOK)):
        command = str(Path(legwork_dir) / "scripts" / script)
        entries = [dict(e) for e in (hooks.get(event) or [])]
        if not _hook_present(entries, script):
            entries.append({"hooks": [{"type": "command", "command": command}]})
        hooks[event] = entries
    settings["hooks"] = hooks
    return settings


def _hook_present(entries, script):
    for group in entries:
        for hook in group.get("hooks", []):
            if script in str(hook.get("command", "")):
                return True
    return False


# --- validators: return (ok, cleaned, error) -------------------------------

def validate_dir(raw):
    expanded = os.path.expanduser(os.path.expandvars(raw.strip()))
    if not expanded:
        return False, None, "a path is required"
    return True, raw.strip(), ""


def validate_int(raw, low=1, high=10_000):
    try:
        n = int(raw.strip())
    except ValueError:
        return False, None, "enter a whole number"
    if not (low <= n <= high):
        return False, None, f"must be between {low} and {high}"
    return True, n, ""


def validate_cost(raw):
    try:
        n = float(raw.strip())
    except ValueError:
        return False, None, "enter a number (0 for no cap)"
    if n < 0:
        return False, None, "cannot be negative"
    return True, n, ""


def validate_url(raw):
    raw = raw.strip()
    if not raw:
        return False, None, "a URL is required for this mode"
    if not re.match(r"^https?://", raw):
        return False, None, "must start with http:// or https://"
    return True, raw, ""


def validate_minutes(raw):
    return validate_int(raw, low=1, high=1440)


# ---------------------------------------------------------------------------
# Rendering: ANSI + ASCII art (degrades to plain text)
# ---------------------------------------------------------------------------

class UI:
    """Output styling that quietly turns itself off when the terminal cannot
    take it (NO_COLOR, --no-color, a pipe, a dumb TERM, a non-UTF-8 stdout).
    Everything routes through here so the wizard reads the same whether it is
    painting a colored box or a plain one."""

    PAL = ["38;5;51", "38;5;45", "38;5;39", "38;5;33", "38;5;27"]  # cyan->blue

    def __init__(self, color=True, unicode=True):
        self.color = color
        self.unicode = unicode
        if unicode:
            self.box = dict(tl="╭", tr="╮", bl="╰", br="╯", h="─", v="│")
            self.rule = "─"
            self.hrule = "━"
            self.check = "✓"
            self.cross = "✗"
            self.arrow = "›"
            self.flow = "─▸"
            self.on = "█"
            self.dot_on = "◆"
            self.dot_off = "◇"
            self.spark = "✦"
            self.warn = "▲"
        else:
            self.box = dict(tl="+", tr="+", bl="+", br="+", h="-", v="|")
            self.rule = "-"
            self.hrule = "="
            self.check = "OK"
            self.cross = "x"
            self.arrow = ">"
            self.flow = "->"
            self.on = "#"
            self.dot_on = "#"
            self.dot_off = "-"
            self.spark = "*"
            self.warn = "!"

    def c(self, text, code):
        if not self.color or not code:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, t):
        return self.c(t, "1")

    def dim(self, t):
        return self.c(t, "2")

    def accent(self, t):
        return self.c(t, "38;5;44")

    def good(self, t):
        return self.c(t, "38;5;42")

    def warnc(self, t):
        return self.c(t, "38;5;214")

    def bad(self, t):
        return self.c(t, "38;5;203")


# A 5-row block font for the LEGWORK wordmark. Each glyph is rows of '#';
# the renderer pads ragged rows so columns always line up.
GLYPHS = {
    "L": ["#", "#", "#", "#", "####"],
    "E": ["####", "#", "###", "#", "####"],
    "G": [" ###", "#", "# ##", "#  #", " ###"],
    "W": ["#   #", "#   #", "# # #", "## ##", "#   #"],
    "O": [" ## ", "#  #", "#  #", "#  #", " ## "],
    "R": ["### ", "#  #", "### ", "# # ", "#  #"],
    "K": ["#  #", "# # ", "##  ", "# # ", "#  #"],
}


def wordmark(text, ui):
    """The block-letter wordmark as a list of 5 colored rows. Glyphs are
    zipped row-wise after padding, so alignment can never drift."""
    glyphs = [GLYPHS[ch] for ch in text if ch in GLYPHS]
    widths = [max(len(r) for r in g) for g in glyphs]
    rows = []
    for i in range(5):
        cells = [g[i].ljust(w) for g, w in zip(glyphs, widths)]
        line = "  ".join(cells)
        painted = line.replace("#", ui.on)
        if ui.color:
            painted = f"\033[{ui.PAL[i]}m{painted}\033[0m"
        rows.append(painted)
    return rows


def masthead(ui):
    """The framed banner: the wordmark, a tagline, and the pipeline motif."""
    inner = 58
    b = ui.box
    sub1 = "autonomous project queue for Claude Code"
    flow = ui.flow
    sub2 = f"projects {flow} runner {flow} session {flow} review {flow} you"
    lines = []
    lines.append(ui.accent(b["tl"] + b["h"] * inner + b["tr"]))

    def row(content="", pad_left=3, painted_len=None):
        # painted_len lets us account for ANSI codes that do not occupy cells.
        visible = painted_len if painted_len is not None else len(content)
        right = inner - pad_left - visible
        right = max(right, 0)
        body = " " * pad_left + content + " " * right
        return ui.accent(b["v"]) + body + ui.accent(b["v"])

    lines.append(row())
    for i, wm in enumerate(wordmark("LEGWORK", ui)):
        # The painted wordmark carries color codes; its visible width is fixed.
        raw_len = len("  ".join(
            g[i].ljust(max(len(r) for r in g))
            for g in (GLYPHS[c] for c in "LEGWORK")))
        lines.append(row(wm, pad_left=5, painted_len=raw_len))
    lines.append(row())
    lines.append(row(ui.bold(sub1), painted_len=len(sub1)))
    lines.append(row(ui.dim(sub2), painted_len=len(sub2)))
    lines.append(row())
    lines.append(ui.accent(b["bl"] + b["h"] * inner + b["br"]))
    return "\n".join(lines)


def progress(ui, step, total, title):
    """A step header: filled/empty markers, the count, and a heavy rule."""
    dots = "".join(ui.dot_on if i < step else ui.dot_off for i in range(total))
    head = (f"{ui.accent(dots)}  {ui.bold(title)}  "
            f"{ui.dim(f'step {step} of {total}')}")
    return "\n" + head + "\n" + ui.dim(ui.hrule * 60)


# ---------------------------------------------------------------------------
# Interactive layer
# ---------------------------------------------------------------------------

class Wizard:
    def __init__(self, ui, assume_yes=False):
        self.ui = ui
        self.assume_yes = assume_yes

    def ask(self, label, help_text="", default="", validate=None):
        ui = self.ui
        print()
        print("  " + ui.bold(label))
        if help_text:
            print("  " + ui.dim(help_text))
        shown = f"[{default}] " if default != "" else ""
        while True:
            if self.assume_yes:
                raw = ""
            else:
                try:
                    raw = input(f"  {ui.dim(shown)}{ui.accent(ui.arrow)} ")
                except EOFError:
                    raw = ""
            if raw.strip() == "" and default != "":
                raw = str(default)
            if validate is None:
                return raw.strip()
            ok, cleaned, err = validate(raw)
            if ok:
                return cleaned
            print("  " + ui.bad(f"{ui.cross} {err}"))

    def ask_yn(self, question, default=True):
        ui = self.ui
        if self.assume_yes:
            return default
        suffix = "[Y/n]" if default else "[y/N]"
        while True:
            try:
                raw = input(f"  {ui.bold(question)} {ui.dim(suffix)} "
                            f"{ui.accent(ui.arrow)} ").strip().lower()
            except EOFError:
                return default
            if raw == "":
                return default
            if raw in ("y", "yes"):
                return True
            if raw in ("n", "no"):
                return False
            print("  " + ui.bad(f"{ui.cross} please answer y or n"))

    def ask_choice(self, label, help_text, options, default_index=0):
        """options: list of (key, summary). Returns the chosen key."""
        ui = self.ui
        print()
        print("  " + ui.bold(label))
        if help_text:
            print("  " + ui.dim(help_text))
        for i, (_key, summary) in enumerate(options, 1):
            mark = ui.accent(str(i))
            print(f"    {mark}) {summary}")
        while True:
            if self.assume_yes:
                return options[default_index][0]
            try:
                raw = input(f"  {ui.dim(f'[{default_index + 1}] ')}"
                            f"{ui.accent(ui.arrow)} ").strip()
            except EOFError:
                return options[default_index][0]
            if raw == "":
                return options[default_index][0]
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1][0]
            print("  " + ui.bad(f"{ui.cross} pick a number 1-{len(options)}"))


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def detect_python():
    """The interpreter the timer should run. macOS launchd is happiest with
    the system python (it does not depend on a shell-managed Python), so we
    prefer /usr/bin/python3 when it exists, then this interpreter."""
    system = Path("/usr/bin/python3")
    if system.exists():
        return str(system)
    found = shutil.which("python3")
    return found or sys.executable


def collect_values(wiz, existing):
    """Walk every configurable variable. `existing` pre-fills defaults from a
    previously written config so a re-run keeps your answers."""
    ui = wiz.ui

    print(progress(ui, 1, 5, "Where legwork lives"))
    legwork_dir = wiz.ask(
        "Legwork directory",
        "Holds projects/, runner.log, the dashboard and runner state. "
        "This checkout is the natural home.",
        default=existing.get("LEGWORK_DIR", str(REPO)),
        validate=validate_dir)

    print(progress(ui, 2, 5, "Firing limits"))
    daily_cap = wiz.ask(
        "Daily fire cap per project",
        "Autonomous sessions one project may fire in a calendar day.",
        default=existing.get("LEGWORK_DAILY_CAP", "8"),
        validate=validate_int)
    daily_cost_cap = wiz.ask(
        "Daily cost cap in USD",
        "A spend guard across all projects per day. 0 means no cap.",
        default=existing.get("LEGWORK_DAILY_COST_CAP", "0"),
        validate=validate_cost)

    print(progress(ui, 3, 5, "Review pipeline"))
    review_mode = wiz.ask_choice(
        "How should finished sessions be reviewed?",
        "Reviewer-by-exception is the idea worth stealing: a reviewer reads "
        "every session and only escalates to you when a human decision is "
        "genuinely needed.",
        [("local", "Local  - the runner triages each session with claude -p "
                    "(no n8n)"),
         ("n8n", "n8n    - POST evidence to an n8n review pipeline"),
         ("off", "Off    - just fire and wrap; skip review entirely")],
        default_index=0)

    webhook_url = existing.get("LEGWORK_WEBHOOK_URL", "")
    alert_url = existing.get("LEGWORK_ALERT_URL", "")
    reviewer_model = existing.get("REVIEWER_MODEL", DEFAULT_REVIEWER_MODEL)
    if review_mode == "n8n":
        webhook_url = wiz.ask(
            "Review webhook URL",
            "The n8n webhook that receives session evidence for review.",
            default=webhook_url, validate=validate_url)
        alert_url = wiz.ask(
            "Alert webhook URL (optional)",
            "Receives stall alerts and the daily heartbeat. Blank to skip.",
            default=alert_url)
    if review_mode in ("local", "n8n"):
        reviewer_model = wiz.ask(
            "Reviewer model",
            "A full model id or a short alias.",
            default=reviewer_model or DEFAULT_REVIEWER_MODEL)

    print(progress(ui, 4, 5, "Claude account"))
    use_dedicated = wiz.ask_yn(
        "Run autonomous sessions under a dedicated Claude config dir?",
        default=bool(existing.get("CLAUDE_CONFIG_DIR")))
    claude_config_dir = ""
    if use_dedicated:
        claude_config_dir = wiz.ask(
            "Claude config dir",
            "Sessions run under this dir instead of inheriting your "
            "interactive account.",
            default=existing.get("CLAUDE_CONFIG_DIR", "$HOME/.claude-legwork"),
            validate=validate_dir)

    print(progress(ui, 5, 5, "Tick interval"))
    minutes = wiz.ask(
        "Minutes between ticks",
        "How often the runner wakes to fire eligible projects.",
        default="5", validate=validate_minutes)

    return {
        "legwork_dir": legwork_dir,
        "daily_cap": daily_cap,
        "daily_cost_cap": daily_cost_cap,
        "review_mode": review_mode,
        "webhook_url": webhook_url,
        "alert_url": alert_url,
        "reviewer_model": reviewer_model,
        "claude_config_dir": claude_config_dir,
        "interval_minutes": int(minutes),
    }


def review_screen(ui, config_text):
    """Show the config that will be written before anything touches disk."""
    print("\n" + ui.bold("  Configuration to write") + "  "
          + ui.dim("(config, gitignored)"))
    print(ui.dim("  " + ui.rule * 58))
    for line in config_text.splitlines():
        if line.startswith("#") or line == "":
            print("  " + ui.dim(line))
        else:
            key, _, val = line.partition("=")
            print(f"  {ui.accent(key)}={ui.bold(val)}")
    print(ui.dim("  " + ui.rule * 58))


def write_repo_files(values):
    """Write `config`, and create projects/ and .runner-logs/. Returns the
    list of human-readable actions taken, for the closing summary."""
    actions = []
    legwork_dir = Path(os.path.expanduser(os.path.expandvars(
        values["legwork_dir"])))
    config_path = REPO / "config"
    config_text = render_config(values)
    config_path.write_text(config_text, encoding="utf-8")
    actions.append(f"wrote {config_path}")

    for sub in ("projects", ".runner-logs"):
        target = legwork_dir / sub
        created = not target.exists()
        target.mkdir(parents=True, exist_ok=True)
        actions.append(("created " if created else "have ") + str(target))
    return actions


def _confirm(wiz, force, question, default=True):
    """A yes/no decision for a step with side effects. `force` short-circuits
    the prompt: True or False is used as the answer, None falls back to asking.
    Non-interactive installs pass force so they never act on anything outside
    the repo without an explicit opt-in."""
    if force is not None:
        return force
    return wiz.ask_yn(question, default=default)


def install_verbs(wiz, values, force=None):
    """Copy the slash commands and the legwork-tracker skill into user-level
    `~/.claude`, so /add, /wrap, /pickup, /vision, /log and /shelve work from
    any repo instead of only inside this checkout. Asks first since it writes
    outside the repo; `force` (see _confirm) lets a non-interactive run skip
    it, or accept it with --with-commands. Re-running refreshes the copies
    (e.g. after a git pull), overwriting earlier ones."""
    ui = wiz.ui
    actions = []
    dest_base = Path.home() / ".claude"
    if not _confirm(wiz, force,
            "Install the slash commands (/add, /wrap, /pickup, ...) and the "
            f"legwork-tracker skill into {dest_base}, so they work from any "
            "repo?"):
        actions.append(ui.dim(
            "skipped commands/skill; the verbs only work inside this "
            "checkout (see SETUP.md step 2)"))
        return actions
    pairs = plan_verb_installs(REPO / ".claude", dest_base)
    for src, dest in pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    actions.append(ui.good(
        f"{ui.check} installed {len(pairs)} command/skill files "
        f"under {dest_base}"))
    legwork_dir = os.path.expanduser(os.path.expandvars(values["legwork_dir"]))
    if Path(legwork_dir) != Path.home() / "legwork":
        actions.append(ui.warnc(
            f"{ui.warn} the commands look for the legwork repo at "
            "$LEGWORK_DIR (falling back to ~/legwork); add "
            f"'export LEGWORK_DIR={legwork_dir}' to your shell profile"))
    return actions


def install_timer(wiz, values, force=None):
    """Install and load the launchd agent (macOS) or a crontab line (Linux),
    asking before the system-level action. `force` (see _confirm) lets a
    non-interactive run skip it, or accept it with --with-launchd, without a
    prompt. Returns action strings."""
    ui = wiz.ui
    actions = []
    legwork_dir = os.path.expanduser(os.path.expandvars(values["legwork_dir"]))
    python_bin = values.get("python_bin") or detect_python()
    interval_seconds = values["interval_minutes"] * 60

    if sys.platform == "darwin":
        if not _confirm(wiz, force,
                "Install and load the launchd agent now "
                f"(ticks every {values['interval_minutes']} min)?"):
            actions.append(ui.dim("skipped launchd (see SETUP.md step 5a)"))
            return actions
        template = PLIST_TEMPLATE.read_text(encoding="utf-8")
        plist = render_plist(template, legwork_dir, python_bin,
                             interval_seconds)
        dest = Path.home() / "Library" / "LaunchAgents" / PLIST_NAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(plist, encoding="utf-8")
        actions.append(f"wrote {dest}")
        subprocess.run(["launchctl", "unload", str(dest)],
                       capture_output=True)
        result = subprocess.run(["launchctl", "load", str(dest)],
                                capture_output=True, text=True)
        if result.returncode == 0:
            actions.append(ui.good(f"{ui.check} loaded launchd agent"))
        else:
            actions.append(ui.warnc(
                f"{ui.warn} launchctl load failed: "
                f"{result.stderr.strip() or 'see output'}; "
                f"load it by hand from {dest}"))
    else:
        schedule = cron_schedule(values["interval_minutes"])
        line = render_crontab_line(legwork_dir, python_bin, schedule)
        if not _confirm(wiz, force,
                f"Add a crontab line ticking '{schedule}'?"):
            actions.append(ui.dim("skipped crontab; line to add:"))
            actions.append(ui.dim("  " + line))
            return actions
        existing = subprocess.run(["crontab", "-l"],
                                  capture_output=True, text=True)
        lines = [ln for ln in existing.stdout.splitlines()
                 if CRON_MARKER not in ln] if existing.returncode == 0 else []
        lines.append(line)
        proc = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                              text=True, capture_output=True)
        if proc.returncode == 0:
            actions.append(ui.good(f"{ui.check} installed crontab line"))
        else:
            actions.append(ui.warnc(
                f"{ui.warn} crontab install failed; add by hand:\n  {line}"))
    return actions


def install_hooks(wiz, values, force=None):
    """Register the SessionStart/SessionEnd hooks in the relevant Claude
    settings.json, asking first since it lives outside the repo. `force` (see
    _confirm) lets a non-interactive run skip it, or accept it with
    --with-hooks, without a prompt."""
    ui = wiz.ui
    actions = []
    legwork_dir = os.path.expanduser(os.path.expandvars(values["legwork_dir"]))
    config_dir = values.get("claude_config_dir")
    if config_dir:
        base = Path(os.path.expanduser(os.path.expandvars(config_dir)))
    else:
        base = Path.home() / ".claude"
    settings_path = base / "settings.json"

    if not _confirm(wiz, force,
            f"Register the review hooks in {settings_path}?"):
        actions.append(ui.dim("skipped hooks (see SETUP.md step 4)"))
        return actions

    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            actions.append(ui.warnc(
                f"{ui.warn} {settings_path} is not valid JSON; left it alone. "
                "Add the hooks by hand (SETUP.md step 4)."))
            return actions

    merged = merge_hooks(settings, legwork_dir)
    base.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(merged, indent=2) + "\n",
                             encoding="utf-8")
    actions.append(ui.good(f"{ui.check} registered hooks in {settings_path}"))
    return actions


def closing(ui, actions):
    print("\n" + ui.good(ui.box["tl"] + ui.box["h"] * 58 + ui.box["tr"]))
    title = f"{ui.spark} legwork is installed"
    print(ui.good(ui.box["v"]) + "  " + ui.bold(title).ljust(
        56 + (len(ui.bold(title)) - len(title))) + ui.good(ui.box["v"]))
    print(ui.good(ui.box["bl"] + ui.box["h"] * 58 + ui.box["br"]))
    print()
    for action in actions:
        print(f"  {ui.dim(ui.arrow)} {action}")
    print()
    print("  " + ui.bold("Next:"))
    print("  " + ui.dim(f"{ui.arrow} add a project with /add, close a "
                        "session with /wrap"))
    print("  " + ui.dim(f"{ui.arrow} grant autonomy per project with /vision"))
    print("  " + ui.dim(f"{ui.arrow} verify: python3 scripts/legwork_runner.py "
                        "--doctor"))
    print()


def run_doctor(wiz):
    if wiz.ask_yn("Run the preflight doctor now?", default=True):
        print()
        subprocess.run([sys.executable,
                        str(REPO / "scripts" / "legwork_runner.py"),
                        "--doctor"],
                       env={**os.environ,
                            "LEGWORK_CONFIG": str(REPO / "config")})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def detect_unicode():
    enc = (sys.stdout.encoding or "").lower()
    return "utf" in enc


def detect_color(force_off):
    if force_off or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Interactive installer for legwork.")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="accept every default and do not prompt; the "
                             "outside-the-repo steps (commands, launchd/cron, "
                             "hooks) are skipped unless --with-commands / "
                             "--with-launchd / --with-hooks")
    parser.add_argument("--no-color", action="store_true",
                        help="plain output, no ANSI color")
    parser.add_argument("--with-commands", action="store_true",
                        help="copy the slash commands and skill into "
                             "user-level ~/.claude without prompting "
                             "(use with --yes for a headless install)")
    parser.add_argument("--with-launchd", action="store_true",
                        help="install the launchd/cron timer without prompting "
                             "(use with --yes for a headless install)")
    parser.add_argument("--with-hooks", action="store_true",
                        help="register the Claude hooks without prompting "
                             "(use with --yes for a headless install)")
    args = parser.parse_args(argv)

    ui = UI(color=detect_color(args.no_color), unicode=detect_unicode())

    interactive = sys.stdin.isatty()
    if not interactive and not args.yes:
        print("legwork installer needs a terminal. Re-run with --yes to "
              "accept every default non-interactively.", file=sys.stderr)
        return 2

    assume_yes = args.yes or not interactive
    wiz = Wizard(ui, assume_yes=assume_yes)
    # The command install, the launchd/cron timer and the Claude hooks touch
    # state outside the repo, so a non-interactive run must not perform them
    # silently: under --yes they are skipped unless the matching --with-* flag
    # opts in. Interactive runs still ask (force=None).
    force_verbs = (True if args.with_commands
                   else (False if assume_yes else None))
    force_timer = True if args.with_launchd else (False if assume_yes else None)
    force_hooks = True if args.with_hooks else (False if assume_yes else None)

    print(masthead(ui))

    existing = {}
    config_path = REPO / "config"
    if config_path.exists():
        existing = parse_config_text(config_path.read_text(encoding="utf-8"))
        print("\n  " + ui.dim(
            f"{ui.spark} found an existing config; its values pre-fill below."))

    if shutil.which("claude") is None:
        print("\n  " + ui.warnc(
            f"{ui.warn} the `claude` CLI is not on PATH. The runner shells out "
            "to it; install it before the timer fires."))

    values = collect_values(wiz, existing)
    values["python_bin"] = detect_python()

    config_text = render_config(values)
    review_screen(ui, config_text)
    if not wiz.ask_yn("Write this config and continue?", default=True):
        print("\n  " + ui.dim("nothing written. Re-run when ready."))
        return 0

    actions = []
    actions += write_repo_files(values)
    actions += install_verbs(wiz, values, force=force_verbs)
    actions += install_timer(wiz, values, force=force_timer)
    actions += install_hooks(wiz, values, force=force_hooks)

    closing(ui, actions)
    run_doctor(wiz)
    return 0


if __name__ == "__main__":
    sys.exit(main())
