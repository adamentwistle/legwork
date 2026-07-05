#!/usr/bin/env bash
#
# docs/loop-demo.sh — the presenter behind docs/loop.gif.
#
# This is NOT a live Claude session. Real /wrap and /pickup run a full Claude
# Code session: slow, and different every time. This script reproduces what
# those two verbs actually PRINT, at a fixed pace, so docs/loop.gif re-renders
# deterministically (docs/loop.tape drives it through vhs).
#
# Faithfulness rule: the wrapped log line and the minted next prompt shown for
# /wrap, and the whole /pickup briefing, are taken verbatim in substance from
# examples/projects/garden-planner.md as committed. /wrap mints exactly the
# "## Next prompt" that lives in that file; /pickup reads it straight back.
# If that example file changes, update this script to match.
#
set -u

# --- palette (Claude Code's amber accent + dim chrome) ------------------------
ACC=$'\033[38;5;173m'   # amber accent: the ">" and the tool marker
TXT=$'\033[1;97m'       # bright white: the command you typed
DIM=$'\033[38;5;245m'   # dim gray: tool activity, labels
BODY=$'\033[38;5;253m'  # near-white body text
RST=$'\033[0m'

# typewriter for the command a human types after the ">"
type_cmd() {
  local s=$1 i
  for (( i = 0; i < ${#s}; i++ )); do
    printf '%s' "${s:i:1}"
    sleep 0.05
  done
  printf '\n'
}

# a "Label   value" briefing row: fixed-width dim label, body value
row() {
  printf '  %s%-9s%s %s%s%s\n' "$DIM" "$1" "$RST" "$BODY" "$2" "$RST"
}

pause() { sleep "$1"; }

clear
pause 0.6

# ── /wrap: close the session, mint the next prompt ───────────────────────────
printf '%s> %s' "$ACC" "$TXT"
type_cmd "/wrap garden-planner"
printf '%s' "$RST"
pause 0.7

printf '%s●%s %sUpdated projects/garden-planner.md, rebuilt the dashboard, committed and pushed.%s\n' \
  "$ACC" "$RST" "$DIM" "$RST"
pause 0.7
printf '\n'
row "Logged" "spacing layout now packs rows by mature plant width; added the layout test."
pause 0.5
row "Next" "add a --frost-dates override to \`garden plan\`, recompute the calendar from"
printf '  %s%-9s%s %s%s%s\n' "$DIM" "" "$RST" "$BODY" "the two dates, cover it with a test.  Model: sonnet" "$RST"
pause 2.2

printf '\n\n'

# ── /pickup: days later, a cold session briefs back in ───────────────────────
printf '%s> %s' "$ACC" "$TXT"
type_cmd "/pickup garden-planner"
printf '%s' "$RST"
pause 0.7

printf '%s●%s %sRead projects/garden-planner.md.%s\n' "$ACC" "$RST" "$DIM" "$RST"
pause 0.7
printf '\n'
row "" "Garden Planner — a stdlib Python CLI: a hardiness zone and bed size become a"
row "" "planting calendar of sow, transplant and harvest weeks."
pause 0.4
row "Stands" "queued, autonomy on. plan/list work; calendar wired to the zone frost"
row "" "table across 22 vegetables."
pause 0.4
row "Last" "spacing layout now packs rows by mature plant width, with a layout test."
pause 0.4
row "Next" "add a --frost-dates override to \`garden plan\` and cover it with a test."
pause 0.4
row "Done when" "the override shifts every sow/harvest week and unittest passes."
pause 0.9
printf '\n'
printf '  %sRun the next prompt as written, or adjust it first?%s\n' "$ACC" "$RST"
pause 2.6
