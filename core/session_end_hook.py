#!/usr/bin/env python3
"""SessionEnd hook for Claude Code.

With LEGWORK_WEBHOOK_URL set: gathers evidence about what the session
changed and posts it to the n8n review webhook. Without it (a level 1 /
lite install, or a level 2 install using the local reviewer): rebuilds
the dashboard instead, so the queue page reflects what the session just
wrapped. Fails quietly either way: a broken hook should never block work.
Every invocation is logged to $LEGWORK_DIR/hook.log, including skips.

Hook input arrives as JSON on stdin (session_id, cwd, reason, ...).
Reference: https://code.claude.com/docs/en/hooks
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from legwork_common import legwork_dir, load_config, python_exe  # noqa: E402

POST_TIMEOUT = 15
TRACKER_CHARS = 4500  # frontmatter, Vision and prompt without flooding
TEST_TAIL = 40
DIRTY_LIST_MAX = 15
DIFF_STAT_MAX = 20
SESSION_COMMITS_MAX = 10


def log(log_file, message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        # newline="" keeps the log LF-only even where git's autocrlf is true.
        with open(log_file, "a", encoding="utf-8", newline="") as fh:
            fh.write(f"{stamp}  {message}\n")
    except OSError:
        pass


def git(args, cwd, default=""):
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                                text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return default
    if result.returncode != 0:
        return default
    return result.stdout.strip() or default


def resolve_project_stem(projects_dir, cwd, fallback):
    """A session's identity is its project file stem, not the repo folder name
    (a project's tracker file name may differ from its repo folder). Resolve
    the cwd against each project's repo: frontmatter so the review letter, the
    reply capture and the tracker entry all use the same name. Prefer a running
    project when two share one repo; fall back to the folder name when
    untracked."""
    target = os.path.realpath(cwd)
    matches = []
    try:
        names = sorted(os.listdir(projects_dir))
    except OSError:
        return fallback
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
        if path == target:
            status = re.search(r"^status:[ \t]*(\S+)", head, re.M)
            running = status is not None and status.group(1) == "running"
            matches.append((0 if running else 1, name[:-3]))
    return sorted(matches)[0][1] if matches else fallback


def post(url, payload):
    """POST the payload and return the HTTP status as a 3-digit string.
    "000" means the request never got an answer (refused, DNS, timeout) --
    the same token curl's %{http_code} writes, which the runner reads."""
    request = urllib.request.Request(
        url, data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=POST_TIMEOUT) as response:
            return f"{response.status:03d}"
    except urllib.error.HTTPError as exc:
        return f"{exc.code:03d}"  # a real answer, just not a 2xx
    except Exception:
        return "000"


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    reason = str(payload.get("reason") or "")
    session_id = str(payload.get("session_id") or "")
    cwd = str(payload.get("cwd") or "") or os.environ.get(
        "CLAUDE_PROJECT_DIR") or os.getcwd()

    load_config()
    root = legwork_dir()
    log_file = root / "hook.log"
    repo_name = Path(cwd).name

    # Sessions that ended via clear or resume are restarting, not finishing.
    if reason in ("clear", "resume"):
        log(log_file, f"{repo_name}  skipped: reason={reason}")
        return 0

    # The SessionStart marker pins the repo this session opened in and its HEAD
    # at open, as "<sha> <repo path>". Trust that repo, not the hook's own cwd:
    # an autonomous session ends with its working directory in the legwork repo
    # (it cd'd there to /wrap), which would otherwise misfile the review as a
    # legwork session and diff the start sha against the wrong repo. Fall back
    # to the cwd only when the marker is missing or pre-upgrade (a bare sha).
    start_head = ""
    start_repo = ""
    if session_id:
        start_file = root / ".session-heads" / session_id
        try:
            marker = start_file.read_text(encoding="utf-8").strip()
        except OSError:
            marker = ""
        if marker:
            try:
                start_file.unlink()
            except OSError:
                pass
            start_head, _, rest = marker.partition(" ")
            start_repo = rest.strip()

    # No webhook means no reviewer to notify (a level 1 / lite install, or a
    # level 2 install using the local reviewer). Make the hook earn its keep
    # anyway: rebuild the dashboard so the queue page reflects whatever this
    # session's /wrap just wrote to the tracker. The marker above is still
    # consumed so .session-heads/ does not accumulate. Never log "sent:" here;
    # the runner matches that token to decide a review was delivered.
    webhook = os.environ.get("LEGWORK_WEBHOOK_URL", "")
    if not webhook:
        builder = root / "core" / "build_dashboard.py"
        ok = False
        if builder.is_file():
            try:
                ok = subprocess.run(
                    [python_exe(), str(builder)], capture_output=True,
                    timeout=120).returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
        if ok:
            log(log_file, f"{repo_name}  rebuilt dashboard: no webhook set, "
                          f"reason={reason or 'manual'}")
        else:
            log(log_file, f"{repo_name}  skipped: no webhook set and the "
                          f"dashboard rebuild failed")
        return 0

    work_dir = cwd
    if start_repo and Path(start_repo, ".git").exists():
        work_dir = start_repo
    repo_name = Path(work_dir).name

    if not Path(work_dir).is_dir():
        log(log_file, f"{repo_name}  skipped: cwd unreachable")
        return 0

    repo_name = resolve_project_stem(root / "projects", work_dir, repo_name)

    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], work_dir, "none")
    last_commit = git(["log", "-1", "--oneline"], work_dir, "none")
    porcelain = git(["status", "--porcelain"], work_dir)
    dirty_lines = porcelain.splitlines() if porcelain else []
    dirty_count = str(len(dirty_lines))
    dirty_list = "\n".join(dirty_lines[:DIRTY_LIST_MAX])

    # Session-scoped evidence: diff from the HEAD recorded at SessionStart, in
    # the repo the marker pinned, so the reviewer judges what this session did,
    # not the repo's last commit.
    session_commits = ""
    if start_head:
        diff_stat = "\n".join(
            git(["diff", "--stat", f"{start_head}..HEAD"],
                work_dir).splitlines()[-DIFF_STAT_MAX:])
        session_commits = "\n".join(
            git(["log", "--oneline", f"{start_head}..HEAD"],
                work_dir).splitlines()[:SESSION_COMMITS_MAX])
        if not session_commits and dirty_count == "0":
            log(log_file, f"{repo_name}  skipped: no changes this session "
                          f"(session={session_id})")
            return 0
    else:
        # No start marker (manual test or pre-upgrade session): fall back to
        # the last commit, which the reviewer is told may predate the session.
        diff_stat = "\n".join(
            git(["diff", "--stat", "HEAD~1..HEAD"],
                work_dir).splitlines()[-DIFF_STAT_MAX:])

    # The reviewer needs intent, not just evidence. Send the project's tracker
    # entry (the prompt this session was meant to execute, and the Vision when
    # one exists) when the project is tracked.
    tracker_entry = ""
    tracker_file = root / "projects" / f"{repo_name}.md"
    try:
        tracker_entry = tracker_file.read_text(
            encoding="utf-8")[:TRACKER_CHARS]
    except OSError:
        pass

    # Optional test evidence. Have your sessions write test output here so the
    # reviewer judges evidence, not prose: <repo>/.legwork/last_test_output.txt
    test_output = ""
    try:
        test_output = "\n".join(
            Path(work_dir, ".legwork", "last_test_output.txt").read_text(
                encoding="utf-8", errors="replace").splitlines()[-TEST_TAIL:])
    except OSError:
        pass

    body = json.dumps({
        "source": "claude-code-session-end",
        "repo": repo_name,
        "branch": branch,
        "last_commit": last_commit,
        "diff_stat": diff_stat,
        "session_commits": session_commits,
        "uncommitted_files": dirty_count,
        "uncommitted_list": dirty_list,
        "test_output": test_output,
        "tracker_entry": tracker_entry,
        "session_id": session_id,
        "end_reason": reason,
    })

    code = post(webhook, body)

    # Only log "sent:" on a 2xx response. The runner matches that token to
    # decide the review was delivered; on any non-2xx (404/500/000) log a
    # different token so hook_fired_since() stays False and the runner's
    # notify_reviewer fallback fires instead of silently dropping the review.
    token = "sent:" if code.startswith("2") else "post-failed:"
    log(log_file, f"{repo_name}  {token} reason={reason or 'manual'} "
                  f"session={session_id or 'none'} http={code}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # a broken hook must never block work
