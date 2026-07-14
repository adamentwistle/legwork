#!/usr/bin/env python3
"""SessionStart hook for Claude Code.

Records the repo's HEAD at session open, keyed by session id, so the
SessionEnd hook can report only what this session actually changed.
Also prunes markers older than three days: clear/resume endings and
crashed sessions never consume theirs, and they accumulate otherwise.

Hook input arrives as JSON on stdin (session_id, cwd, ...).
Reference: https://code.claude.com/docs/en/hooks

Fails quietly, always exit 0: a broken hook must never block work.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from legwork_common import legwork_dir, load_config  # noqa: E402

STALE_AFTER = 3 * 86400  # markers no SessionEnd ever consumed


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return 0

    cwd = str(payload.get("cwd") or "") or os.environ.get(
        "CLAUDE_PROJECT_DIR") or os.getcwd()

    load_config()
    heads_dir = legwork_dir() / ".session-heads"

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True,
            text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 0
    if not head:
        return 0  # not a git repo, or no commits yet

    # Record the HEAD and the repo this session opened in: "<sha> <repo path>".
    # An autonomous session ends with its working directory in the legwork repo
    # (it cd'd there to /wrap), so SessionEnd cannot trust its own cwd to find
    # the target repo. The marker pins it. The sha stays the first token so a
    # pre-upgrade SessionEnd that reads the whole line still gets a usable value.
    try:
        heads_dir.mkdir(parents=True, exist_ok=True)
        real = os.path.realpath(cwd)
        # newline="" so the marker is LF on every platform: git's autocrlf is
        # commonly true on Windows, and a CRLF here would land inside the repo
        # path that SessionEnd reads back and cds into.
        with open(heads_dir / session_id, "w", encoding="utf-8",
                  newline="") as fh:
            fh.write(f"{head} {real}\n")
    except OSError:
        return 0

    # Markers no SessionEnd ever consumed.
    cutoff = time.time() - STALE_AFTER
    try:
        for marker in heads_dir.iterdir():
            try:
                if marker.is_file() and marker.stat().st_mtime < cutoff:
                    marker.unlink()
            except OSError:
                continue
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # a broken hook must never block work
