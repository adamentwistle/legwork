#!/usr/bin/env python3
"""Shared helpers for the legwork scripts.

Stdlib only, like everything in scripts/. The runner and the dashboard
builder both parse the same project-file shape (frontmatter, the Next prompt
fenced block, dated values), and the runner and installer share the same
config-file KEY=VALUE rules, so that parsing lives here once instead of being
copied into each script. These scripts run with their own directory on
sys.path[0], so `from legwork_common import ...` resolves whether they are
run directly (python3 scripts/<x>.py), imported by the tests, or fired by
launchd. Keep this module dependency-free.
"""

import re
from datetime import date

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
