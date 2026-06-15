#!/usr/bin/env bash
# SessionEnd hook for Claude Code.
# Gathers evidence about what the session changed and posts it to the
# n8n review webhook. Fails quietly: a broken hook should never block work.
# Every invocation is logged to $LEGWORK_DIR/hook.log, including skips.
#
# Requires: LEGWORK_WEBHOOK_URL set in the environment.
# Hook input arrives as JSON on stdin (session_id, cwd, reason, ...).
# Reference: https://code.claude.com/docs/en/hooks

set -u

LEGWORK_DIR="${LEGWORK_DIR:-$HOME/legwork}"
LOG_FILE="$LEGWORK_DIR/hook.log"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" >> "$LOG_FILE" 2>/dev/null
}

INPUT="$(cat)"

# One JSON parse for every field, emitted as shell-safe assignments.
eval "$(printf '%s' "$INPUT" | python3 -c "
import json, shlex, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
for key in ('session_id', 'cwd', 'reason'):
    print('HOOK_' + key.upper() + '=' + shlex.quote(str(d.get(key, ''))))
" 2>/dev/null)"

REASON="${HOOK_REASON:-}"
SESSION_ID="${HOOK_SESSION_ID:-}"
CWD="${HOOK_CWD:-}"
[ -z "$CWD" ] && CWD="${CLAUDE_PROJECT_DIR:-$PWD}"
REPO_NAME="$(basename "$CWD")"

if [ -z "${LEGWORK_WEBHOOK_URL:-}" ]; then
  log "$REPO_NAME  skipped: LEGWORK_WEBHOOK_URL not set"
  exit 0
fi

# Sessions that ended via clear or resume are restarting, not finishing.
case "$REASON" in
  clear|resume)
    log "$REPO_NAME  skipped: reason=$REASON"
    exit 0
    ;;
esac

# The SessionStart marker pins the repo this session opened in and its HEAD
# at open, as "<sha> <repo path>". Trust that repo, not the hook's own cwd:
# an autonomous session ends with its working directory in the legwork repo
# (it cd'd there to /wrap), which would otherwise misfile the review as a
# legwork session and diff the start sha against the wrong repo. Fall back
# to the cwd only when the marker is missing or pre-upgrade (a bare sha).
START_FILE="$LEGWORK_DIR/.session-heads/$SESSION_ID"
START_HEAD=""
START_REPO=""
if [ -n "$SESSION_ID" ] && [ -f "$START_FILE" ]; then
  MARKER="$(cat "$START_FILE" 2>/dev/null)"
  rm -f "$START_FILE"
  START_HEAD="${MARKER%% *}"
  case "$MARKER" in
    *" "*) START_REPO="${MARKER#* }" ;;
  esac
fi

WORK_DIR="$CWD"
[ -n "$START_REPO" ] && [ -d "$START_REPO/.git" ] && WORK_DIR="$START_REPO"
REPO_NAME="$(basename "$WORK_DIR")"

cd "$WORK_DIR" 2>/dev/null || { log "$REPO_NAME  skipped: cwd unreachable"; exit 0; }

# A session's identity is its project file stem, not the repo folder name
# (a project's tracker file name may differ from its repo folder). Resolve
# the cwd against each project's repo: frontmatter so the review letter, the
# reply capture and the tracker entry all use the same name. Prefer a running
# project when two share one repo; fall back to the folder name when untracked.
RESOLVED="$(HOOK_CWD_REAL="$PWD" python3 - "$LEGWORK_DIR/projects" <<'PYEOF' 2>/dev/null
import os, re, sys
projects_dir = sys.argv[1]
cwd = os.path.realpath(os.environ.get("HOOK_CWD_REAL", ""))
matches = []
try:
    names = sorted(os.listdir(projects_dir))
except OSError:
    names = []
for name in names:
    if not name.endswith(".md") or name.startswith("_"):
        continue
    try:
        with open(os.path.join(projects_dir, name), encoding="utf-8") as fh:
            head = fh.read(2000)
    except OSError:
        continue
    repo = re.search(r"^repo:[ \t]*(.+)$", head, re.M)
    if not repo:
        continue
    path = os.path.realpath(os.path.expanduser(repo.group(1).strip()))
    if path == cwd:
        status = re.search(r"^status:[ \t]*(\S+)", head, re.M)
        running = status is not None and status.group(1) == "running"
        matches.append((0 if running else 1, name[:-3]))
if matches:
    print(sorted(matches)[0][1])
PYEOF
)"
[ -n "$RESOLVED" ] && REPO_NAME="$RESOLVED"

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo none)"
LAST_COMMIT="$(git log -1 --oneline 2>/dev/null || echo none)"
DIRTY_COUNT="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
DIRTY_LIST="$(git status --porcelain 2>/dev/null | head -15 || true)"

# Session-scoped evidence: diff from the HEAD recorded at SessionStart, in
# the repo the marker pinned, so the reviewer judges what this session did,
# not the repo's last commit.
SESSION_COMMITS=""
if [ -n "$START_HEAD" ]; then
  DIFF_STAT="$(git diff --stat "$START_HEAD"..HEAD 2>/dev/null | tail -20 || true)"
  SESSION_COMMITS="$(git log --oneline "$START_HEAD"..HEAD 2>/dev/null | head -10 || true)"
  if [ -z "$SESSION_COMMITS" ] && [ "$DIRTY_COUNT" = "0" ]; then
    log "$REPO_NAME  skipped: no changes this session (session=$SESSION_ID)"
    exit 0
  fi
else
  # No start marker (manual test or pre-upgrade session): fall back to the
  # last commit, which the reviewer is told may predate the session.
  DIFF_STAT="$(git diff --stat HEAD~1..HEAD 2>/dev/null | tail -20 || true)"
fi

# The reviewer needs intent, not just evidence. Send the project's tracker
# entry (the prompt this session was meant to execute, and the Vision when
# one exists) when the project is tracked. 4500 chars fits frontmatter,
# Vision and prompt without flooding the reviewer.
TRACKER_ENTRY=""
if [ -f "$LEGWORK_DIR/projects/$REPO_NAME.md" ]; then
  TRACKER_ENTRY="$(head -c 4500 "$LEGWORK_DIR/projects/$REPO_NAME.md")"
fi

# Optional test evidence. Have your sessions write test output here so the
# reviewer judges evidence, not prose: <repo>/.legwork/last_test_output.txt
TEST_OUTPUT=""
if [ -f ".legwork/last_test_output.txt" ]; then
  TEST_OUTPUT="$(tail -40 .legwork/last_test_output.txt)"
fi

PAYLOAD="$(
  REPO="$REPO_NAME" BRANCH="$BRANCH" COMMIT="$LAST_COMMIT" \
  DIFF="$DIFF_STAT" SESSCOMMITS="$SESSION_COMMITS" DIRTY="$DIRTY_COUNT" DIRTYLIST="$DIRTY_LIST" \
  TESTS="$TEST_OUTPUT" TRACKER="$TRACKER_ENTRY" \
  SID="$SESSION_ID" RSN="$REASON" python3 -c "
import json, os
print(json.dumps({
    'source': 'claude-code-session-end',
    'repo': os.environ['REPO'],
    'branch': os.environ['BRANCH'],
    'last_commit': os.environ['COMMIT'],
    'diff_stat': os.environ['DIFF'],
    'session_commits': os.environ['SESSCOMMITS'],
    'uncommitted_files': os.environ['DIRTY'],
    'uncommitted_list': os.environ['DIRTYLIST'],
    'test_output': os.environ['TESTS'],
    'tracker_entry': os.environ['TRACKER'],
    'session_id': os.environ['SID'],
    'end_reason': os.environ['RSN'],
}))
"
)"

HTTP_CODE="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 15 \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "$LEGWORK_WEBHOOK_URL" 2>/dev/null)"
[ -z "$HTTP_CODE" ] && HTTP_CODE=000

log "$REPO_NAME  sent: reason=${REASON:-manual} session=${SESSION_ID:-none} http=$HTTP_CODE"

exit 0
