#!/usr/bin/env bash
# SessionStart hook for Claude Code.
# Records the repo's HEAD at session open, keyed by session id, so the
# SessionEnd hook can report only what this session actually changed.
# Also prunes markers older than three days: clear/resume endings and
# crashed sessions never consume theirs, and they accumulate otherwise.

set -u

LEGWORK_DIR="${LEGWORK_DIR:-$HOME/legwork}"
HEADS_DIR="$LEGWORK_DIR/.session-heads"

INPUT="$(cat)"

# One JSON parse for every field, emitted as shell-safe assignments.
eval "$(printf '%s' "$INPUT" | python3 -c "
import json, shlex, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
for key in ('session_id', 'cwd'):
    print('HOOK_' + key.upper() + '=' + shlex.quote(str(d.get(key, ''))))
" 2>/dev/null)"

SESSION_ID="${HOOK_SESSION_ID:-}"
CWD="${HOOK_CWD:-}"
[ -z "$CWD" ] && CWD="${CLAUDE_PROJECT_DIR:-$PWD}"
[ -z "$SESSION_ID" ] && exit 0

cd "$CWD" 2>/dev/null || exit 0
HEAD_SHA="$(git rev-parse HEAD 2>/dev/null)" || exit 0

# Record the HEAD and the repo this session opened in: "<sha> <repo path>".
# An autonomous session ends with its working directory in the legwork repo
# (it cd'd there to /wrap), so SessionEnd cannot trust its own cwd to find
# the target repo. The marker pins it. The sha stays the first token so a
# pre-upgrade SessionEnd that reads the whole line still gets a usable value.
mkdir -p "$HEADS_DIR" 2>/dev/null
echo "$HEAD_SHA $(pwd -P)" > "$HEADS_DIR/$SESSION_ID" 2>/dev/null

# Markers no SessionEnd ever consumed.
find "$HEADS_DIR" -type f -mtime +3 -delete 2>/dev/null

exit 0
