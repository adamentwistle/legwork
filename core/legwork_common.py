#!/usr/bin/env python3
"""Shared helpers for the legwork scripts.

Stdlib only, like everything under core/ and suite/. The runner and the
dashboard builder both parse the same project-file shape (frontmatter, the
Next prompt fenced block, dated values), and the runner and installer share
the same config-file KEY=VALUE rules, so that parsing lives here once instead
of being copied into each script. This module is the shared base of the whole
dependency graph: core/ scripts sit beside it (sys.path[0] covers a direct
run), and the suite/ scripts and the installer put core/ on sys.path before
importing it: suite imports from core, never the reverse. Keep this module
dependency-free and free of suite/ imports.
"""

import os
import re
import sys
from datetime import date
from pathlib import Path

# The Next prompt is the first fenced block under the "## Next prompt"
# heading. Shared verbatim by the runner (eligibility) and the dashboard
# (card rendering) so the two never disagree on what "the prompt" is.
# The lines between the heading and the fence must not contain another
# `##` heading: a Next prompt section with no fenced block yields no
# match instead of silently binding to a fence in a later section
# (e.g. a shell snippet quoted under `## Log`).
PROMPT_RE = re.compile(
    r"##[ \t]*Next prompt[^\n]*\n"   # the heading line
    r"(?:(?!##)[^\n]*\n)*?"          # gap lines, none opening a new section
    r"```[a-zA-Z]*[ \t]*\n(.*?)```",
    re.S)

# The per-fire dollar cost in a runner.log "completed" line, e.g.
# "... completed foo.md: exit 0, 7 min, $1.23, 5 turns". transcript_summary
# writes it as ${cost:.2f} (always two decimals), and the runner (daily cost
# cap) and the dashboard (per-file spend) read it back with this one pattern
# so the two never disagree on what a fire cost.
COST_RE = re.compile(r"\$(\d+\.\d{2})")


def parse_frontmatter(text):
    """Parse the simple `key: value` frontmatter between the first two `---`
    marker LINES into a dict. One-line values only; the first colon splits.
    The markers must be whole lines: a `---` inside a value (a date range, a
    quoted horizontal rule) is content, not a marker, so it can never
    truncate the block and silently drop the keys after it."""
    meta = {}
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines = lines[1:]
    if not lines or lines[0].strip() != "---":
        return meta
    for line in lines[1:]:
        if line.strip() == "---":
            return meta
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return {}  # no closing marker: this was not frontmatter


def parse_date(value):
    """A leading YYYY-MM-DD (the frontmatter `updated` field or a dated log
    bullet) as a date, or None when it is missing or malformed."""
    try:
        y, m, d = (int(x) for x in value[:10].split("-"))
        return date(y, m, d)
    except (ValueError, AttributeError, TypeError):
        return None


def days_since(value):
    """Whole days from a YYYY-MM-DD value to today, or None when unparseable."""
    d = parse_date(value)
    return (date.today() - d).days if d else None


def iter_config_pairs(text):
    """Yield (key, value) pairs from KEY=VALUE config text, skipping blank
    lines and `#` comments and stripping surrounding quotes. Values are
    returned verbatim: $VARS and ~ are NOT expanded here, so callers expand
    only when they need to (the runner does, into the environment; the
    installer does not, so its prompts echo the file's own text)."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        yield key.strip(), value.strip().strip('"').strip("'")


# The repo root: this module lives at <root>/core/legwork_common.py, and the
# runner, the hooks and the dashboard builder all sit one level below it too.
ROOT = Path(__file__).resolve().parent.parent


def load_config():
    """Load KEY=VALUE lines from a config file into the environment, so
    launchd and Task Scheduler (neither reads your shell profile), cron and
    manual runs share one source of truth. Real environment variables always
    win over the file. The file is looked for at $LEGWORK_CONFIG, else a
    `config` file beside the repo root; a missing file is fine. $VARS and ~
    are expanded so the file stays machine-agnostic. See config.example.

    This lives in core/ rather than in the runner because the hooks need the
    same resolution: they run from a Claude session, which on no platform is
    guaranteed to have seen a shell profile export (and on Windows there is
    no profile to export from at all)."""
    candidates = []
    env_path = os.environ.get("LEGWORK_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(ROOT / "config")
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for key, value in iter_config_pairs(text):
            os.environ.setdefault(
                key, os.path.expanduser(os.path.expandvars(value)))
        break


def legwork_dir():
    """The legwork checkout to operate on: $LEGWORK_DIR (real env or config),
    else the root of the checkout this file belongs to. That fallback beats a
    hardcoded ~/legwork -- the code is already sitting in the answer -- and it
    is what makes a hook correct in a non-interactive context that never saw
    a shell export. Call load_config() first so the config file is folded in."""
    return Path(os.environ.get("LEGWORK_DIR") or ROOT)


def write_lf(path, text):
    """Write text with LF line endings on every platform.

    Python's text mode translates "\\n" to os.linesep on write, so a plain
    write_text() rewrites a file with CRLF on Windows. These files (the
    tracker markdown, the dashboard) are git-tracked and shared with the
    other machines and with n8n, and a Windows checkout commonly runs
    core.autocrlf=true -- so a CRLF rewrite turns every claim() into a
    whole-file diff and a conflict against the machine that wrote it LF."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def python_exe():
    """The interpreter to spawn a child legwork script with.

    Always sys.executable, never the string "python3". On Windows the Python
    installer never creates a python3.exe, so `python3` resolves to a 0-byte
    Microsoft Store stub that exits 9009 with "Python was not found" --
    permanently, and shutil.which("python3") happily RETURNS that stub rather
    than falling through. Verified on Windows 11 26200 with Python 3.12.10."""
    return sys.executable or "python"
