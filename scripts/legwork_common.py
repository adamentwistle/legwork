#!/usr/bin/env python3
"""Shared helpers for the legwork scripts.

Stdlib only, like everything in scripts/. The runner and the dashboard
builder both parse the same project-file shape (frontmatter, the Next prompt
fenced block, dated values), so the parsing lives here once instead of being
copied into each script. Both scripts run with their own directory on
sys.path[0], so `from legwork_common import ...` resolves whether they are
run directly (python3 scripts/<x>.py), imported by the tests, or fired by
launchd. Keep this module dependency-free.
"""

import re
from datetime import date

# The Next prompt is the first fenced block under the "## Next prompt"
# heading. Shared verbatim by the runner (eligibility) and the dashboard
# (card rendering) so the two never disagree on what "the prompt" is.
PROMPT_RE = re.compile(r"##\s*Next prompt.*?```[a-zA-Z]*\n(.*?)```", re.S)


def parse_frontmatter(text):
    """Parse the simple `key: value` frontmatter between the first two `---`
    markers into a dict. One-line values only; the first colon splits."""
    meta = {}
    parts = text.split("---")
    if len(parts) < 3:
        return meta
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta


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
