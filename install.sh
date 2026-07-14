#!/usr/bin/env bash
# legwork installer: a thin wrapper that hands off to the stdlib Python
# wizard in scripts/legwork_install.py. Run it from anywhere:
#
#     ./install.sh            the visual wizard
#     ./install.sh --lite     level 1, the manual loop: no timer
#     ./install.sh --yes      accept every default, no prompts
#     ./install.sh --no-color plain output
#
# The wizard's first question is the install level: level 1 is the manual
# loop (config, projects/, the user-level commands and the webhook-less
# hooks — SessionEnd rebuilds the dashboard; the timer step is never
# reached), level 2 is the full autonomous flow. Re-runs pre-fill your
# previous answer; graduating is re-running and picking 2.
#
# A non-interactive --yes run writes the in-repo config but skips the
# outside-the-repo steps (the user-level command/skill install, the
# launchd/cron timer and the Claude hooks) unless you add --with-commands /
# --with-launchd / --with-hooks, so it never writes to ~/.claude, loads a
# launchd agent or edits your settings.json behind your back. --lite pins
# level 1 headless; --with-launchd pins level 2; --with-hooks works at
# either level; a bare --yes on a fresh clone defaults to level 1.
#
# It only needs python3, git and the Claude Code CLI on PATH; the wizard
# checks for the rest and tells you what is missing.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Find an interpreter that actually RUNS, not merely one that resolves.
# Under Git Bash on Windows `command -v python3` succeeds and hands back a
# 0-byte Microsoft Store stub that exits 9009 without running anything, so
# each candidate has to be probed rather than trusted.
py=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
        py="$candidate"
        break
    fi
done

if [ -z "$py" ]; then
    echo "legwork install: a working python3 was not found on PATH." >&2
    echo "Install Python 3.9+ and re-run ./install.sh" >&2
    echo "On Windows, run the wizard directly instead:" >&2
    echo "    python scripts\\legwork_install.py" >&2
    exit 1
fi

exec "$py" "$here/scripts/legwork_install.py" "$@"
