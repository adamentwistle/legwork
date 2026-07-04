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

if ! command -v python3 >/dev/null 2>&1; then
    echo "legwork install: python3 is required but was not found on PATH." >&2
    echo "Install Python 3.9+ and re-run ./install.sh" >&2
    exit 1
fi

exec python3 "$here/scripts/legwork_install.py" "$@"
