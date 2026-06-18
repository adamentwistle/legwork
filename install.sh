#!/usr/bin/env bash
# legwork installer: a thin wrapper that hands off to the stdlib Python
# wizard in scripts/legwork_install.py. Run it from anywhere:
#
#     ./install.sh            the visual wizard
#     ./install.sh --yes      accept every default, no prompts
#     ./install.sh --no-color plain output
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
