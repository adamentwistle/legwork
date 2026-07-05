"""Tests for the legwork runner, dashboard builder and hooks.

Stdlib only, like everything under core/ and suite/. Run from the repo root:

    python3 -m unittest discover -s tests -v

The runner module reads LEGWORK_DIR at import, so the temp sandbox is
exported before the import below. Hook tests exercise the real shell
scripts as subprocesses against throwaway git repos; nothing here touches
the real legwork repo, the webhook, or the network (the send test posts
to a dead local port and asserts the logged http=000).
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="legwork-test-"))
SANDBOX = TMP / "legwork"

# Seal the sandbox before the imports below read the environment. The
# contributor's real legwork config must not leak into the suite
# (legwork_runner.load_config() runs at import; /dev/null reads clean, which
# also stops the fallback to the repo's own gitignored `config`), and their
# global/system git config must not leak into the throwaway repos or the
# hook subprocesses, which inherit os.environ.
for _key in [k for k in os.environ if k.startswith("LEGWORK_")]:
    del os.environ[_key]
os.environ["LEGWORK_DIR"] = str(SANDBOX)
os.environ["LEGWORK_CONFIG"] = os.devnull
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
# core/ is the shared base, suite/ imports from it, scripts/ holds the
# installer; all three are import roots for the suite.
sys.path.insert(0, str(REPO / "core"))
sys.path.insert(0, str(REPO / "suite"))
sys.path.insert(0, str(REPO / "scripts"))

import build_dashboard  # noqa: E402
import legwork_install  # noqa: E402
import legwork_review  # noqa: E402
import legwork_runner  # noqa: E402

# The guard settings file normally lives in the system temp dir; point it
# into the suite's own TMP so no test can leave a legwork-runner-guard.json
# behind on the contributor's machine (tearDownModule removes all of TMP).
legwork_runner.GUARD_SETTINGS = TMP / "legwork-runner-guard.json"

GIT_ID = ["-c", "user.email=test@test", "-c", "user.name=test"]


def _init_sandbox_repo():
    """Make the sandbox legwork dir a real git clone with a bare origin
    standing in for the GitHub remote. Runs at import, not in any class's
    setUpClass, so no test depends on another class having run first."""
    origin = TMP / "legwork-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                    str(origin)], check=True)
    (SANDBOX / "projects").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=SANDBOX,
                   check=True)
    subprocess.run(["git", "config", "user.email", "test@test"],
                   cwd=SANDBOX, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=SANDBOX,
                   check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)],
                   cwd=SANDBOX, check=True)
    # Mirror the real repo's ignores: runtime files must stay untracked
    # or their churn would read as uncommitted tracked changes and
    # block ticks mid-suite.
    (SANDBOX / ".gitignore").write_text(
        "runner.log\n.runner-state.json\nhook.log\n.session-heads/\n",
        encoding="utf-8")
    return origin


ORIGIN = _init_sandbox_repo()


def commit_and_push(message):
    subprocess.run(["git", "add", "-A"], cwd=SANDBOX, check=True)
    subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                    "-m", message], cwd=SANDBOX, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"],
                   cwd=SANDBOX, check=True)


def remote_commit(relpath, content, message):
    """Move the origin from a second clone, the way the n8n Contents
    API or a parallel session's wrap moves the real remote."""
    clone = Path(tempfile.mkdtemp(prefix="clone-", dir=str(TMP)))
    subprocess.run(["git", "clone", "-q", str(ORIGIN), str(clone)],
                   check=True)
    target = clone / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=clone, check=True)
    subprocess.run(["git", *GIT_ID, "commit", "-q", "-m", message],
                   cwd=clone, check=True)
    subprocess.run(["git", "push", "-q"], cwd=clone, check=True)


def origin_file(relpath):
    return subprocess.run(
        ["git", "show", f"main:{relpath}"], cwd=ORIGIN,
        capture_output=True, text=True).stdout


def origin_subjects():
    return subprocess.run(
        ["git", "log", "--format=%s", "main"], cwd=ORIGIN,
        capture_output=True, text=True).stdout


commit_and_push("init sandbox")


def make_git_repo(name):
    path = TMP / name
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    return path


def write_project(fname, status="queued", autonomy="loop", vision=True,
                  repo="", prompt=None, blocked_on="", updated="2026-06-01",
                  log_lines=None, fire_once=""):
    if prompt is None:
        prompt = ("Read PROJECT.md.\n\nTask: do one thing.\n\n"
                  "Done when: the thing is verifiably done.\n\n"
                  "Final step: run /wrap to update the tracker and mint "
                  "the next prompt.")
    lines = ["---", f"name: {Path(fname).stem}", "category: personal",
             f"status: {status}", "energy: light", "description: test",
             f"repo: {repo or 'none'}", f"updated: {updated}"]
    if autonomy:
        lines.append(f"autonomy: {autonomy}")
    if blocked_on:
        lines.append(f"blocked_on: {blocked_on}")
    if fire_once:
        lines.append(f"fire_once: {fire_once}")
    lines.append("---\n")
    if vision:
        lines.append("## Vision\n\n- North star: a tested thing.\n")
    lines.append("## Next prompt\n")
    lines.append("```text\n" + prompt + "\n```\n")
    lines.append("## Log\n")
    for entry in (log_lines or [f"- {updated}: created for tests."]):
        lines.append(entry)
    path = SANDBOX / "projects" / fname
    # git removes a directory that loses its last tracked file (e.g. a
    # reset rolling back a commit), so recreate it rather than depend on
    # what earlier tests left behind.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestFrontmatter(unittest.TestCase):
    def test_basic(self):
        meta = legwork_runner.parse_frontmatter(
            "---\nname: X\nstatus: queued\n---\nbody")
        self.assertEqual(meta["name"], "X")
        self.assertEqual(meta["status"], "queued")

    def test_missing(self):
        self.assertEqual(legwork_runner.parse_frontmatter("no fences"), {})

    def test_dashes_inside_a_value_do_not_truncate(self):
        # The reviewer's repro: `---` inside a value used to end the block
        # and silently drop every key after it (an autonomy opt-in, a
        # blocked_on). Markers are whole lines, not substrings.
        meta = legwork_runner.parse_frontmatter(
            "---\nname: X\ndescription: range 1---2\nautonomy: loop\n"
            "---\nbody")
        self.assertEqual(meta["description"], "range 1---2")
        self.assertEqual(meta["autonomy"], "loop")

    def test_unclosed_frontmatter_yields_nothing(self):
        self.assertEqual(
            legwork_runner.parse_frontmatter("---\nname: X\nno close"), {})

    def test_leading_blank_lines_tolerated(self):
        meta = legwork_runner.parse_frontmatter("\n\n---\nname: X\n---\n")
        self.assertEqual(meta["name"], "X")


class TestPromptRe(unittest.TestCase):
    """The shared Next-prompt regex must never read past the end of its own
    section: a prompt section with no fenced block yields no match instead of
    binding to a fence quoted under a later heading (e.g. ## Log)."""

    def test_reads_fence_in_own_section(self):
        text = ("## Next prompt\n\n```text\ndo the thing\n```\n\n"
                "## Log\n\n- 2026-07-01: created.\n")
        m = legwork_runner.PROMPT_RE.search(text)
        self.assertEqual(m.group(1).strip(), "do the thing")

    def test_allows_prose_between_heading_and_fence(self):
        text = ("## Next prompt\n\nQueued by hand:\n\n```\nreal prompt\n```\n")
        m = legwork_runner.PROMPT_RE.search(text)
        self.assertEqual(m.group(1).strip(), "real prompt")

    def test_does_not_cross_into_a_later_section(self):
        text = ("## Next prompt\n\nPROMPT NEEDED. Human will fill this in.\n\n"
                "## Log\n\n- 2026-07-01: cleanup notes:\n\n"
                "```bash\nrm -rf build/\n```\n")
        self.assertIsNone(legwork_runner.PROMPT_RE.search(text))

    def test_unfenced_section_assessed_as_no_prompt(self):
        # End to end through assess(): the log fence must not fire.
        repo = make_git_repo("promptre-target")
        path = write_project("promptre.md", repo=str(repo), prompt="x")
        path.write_text(path.read_text(encoding="utf-8").replace(
            "```text\nx\n```\n",
            "PROMPT NEEDED.\n") + "\n```bash\nrm -rf build/\n```\n",
            encoding="utf-8")
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertEqual(reason, "no next prompt")


class TestDirectives(unittest.TestCase):
    def test_model_and_effort_extracted(self):
        prompt = ("Read X.\n\nTask: y.\nModel: opus\nEffort: high\n\n"
                  "Done when: z.")
        clean, model, effort, ignored = legwork_runner.extract_directives(prompt)
        self.assertEqual(model, "opus")
        self.assertEqual(effort, "high")
        self.assertEqual(ignored, [])
        self.assertNotIn("Model:", clean)
        self.assertNotIn("Effort:", clean)
        self.assertIn("Task: y.", clean)

    def test_aliases_pass_through(self):
        _, model, _, _ = legwork_runner.extract_directives("Model: sonnet\nx")
        self.assertEqual(model, "sonnet")

    def test_unknown_values_ignored_but_visible(self):
        clean, model, effort, ignored = legwork_runner.extract_directives(
            "Model: gpt9\nEffort: extreme\nTask: y.")
        self.assertIsNone(model)
        self.assertIsNone(effort)
        self.assertEqual(ignored, ["model=gpt9", "effort=extreme"])
        # Unrecognised values are reported but their lines stay in the
        # prompt: they may be legitimate prose that merely starts with
        # "Model:"/"Effort:", so nothing is silently eaten.
        self.assertIn("Model: gpt9", clean)
        self.assertIn("Effort: extreme", clean)
        self.assertIn("Task: y.", clean)

    def test_absent_directives(self):
        clean, model, effort, ignored = legwork_runner.extract_directives(
            "Task: y.")
        self.assertEqual((model, effort, ignored), (None, None, []))
        self.assertEqual(clean, "Task: y.")


class TestAssess(unittest.TestCase):
    def test_not_queued(self):
        path = write_project("t-review.md", status="review")
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertIn("status is review", reason)

    def test_no_autonomy(self):
        path = write_project("t-noauto.md", autonomy="")
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertIn("no autonomy", reason)

    def test_autonomy_without_vision_refused(self):
        path = write_project("t-novision.md", vision=False)
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertIn("no Vision", reason)

    def test_blocked_on_refused(self):
        repo = make_git_repo("t-blocked-repo")
        path = write_project("t-blocked.md", repo=str(repo),
                             blocked_on="solicitor sign-off")
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertIn("blocked_on: solicitor sign-off", reason)

    def test_marker_prompt_refused(self):
        repo = make_git_repo("t-marker-repo")
        path = write_project("t-marker.md", repo=str(repo),
                             prompt="DECISION NEEDED. Pick A or B.")
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertIn("marker", reason)

    def test_no_repo(self):
        path = write_project("t-norepo.md", repo="none")
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertIn("no repo", reason)

    def test_dirty_target_repo(self):
        repo = make_git_repo("t-dirty-repo")
        (repo / "wip.txt").write_text("x", encoding="utf-8")
        path = write_project("t-dirty.md", repo=str(repo))
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertIn("dirty", reason)

    def test_eligible_with_directives(self):
        repo = make_git_repo("t-ok-repo")
        path = write_project(
            "t-ok.md", repo=str(repo),
            prompt=("Read PROJECT.md.\n\nTask: one thing.\nModel: haiku\n"
                    "Effort: low\n\nDone when: done.\n\nFinal step: run "
                    "/wrap to update the tracker and mint the next prompt."))
        ok, reason, details = legwork_runner.assess(path)
        self.assertTrue(ok)
        self.assertIn("model=haiku", reason)
        self.assertEqual(details["model"], "haiku")
        self.assertEqual(details["effort"], "low")
        self.assertNotIn("Model:", details["prompt"])

    def test_fire_once_stands_in_for_autonomy_and_vision(self):
        repo = make_git_repo("t-fonce-repo")
        path = write_project("t-fonce.md", autonomy="", vision=False,
                             repo=str(repo), fire_once="2026-06-11")
        ok, reason, details = legwork_runner.assess(path)
        self.assertTrue(ok)
        self.assertIn("fire_once", reason)
        self.assertFalse(details["has_vision"])

    def test_fire_once_does_not_bypass_blocked_on(self):
        repo = make_git_repo("t-fonce-blocked-repo")
        path = write_project("t-fonce-blocked.md", autonomy="", vision=False,
                             repo=str(repo), fire_once="2026-06-11",
                             blocked_on="waiting on supplier")
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertIn("blocked_on", reason)

    def test_fire_once_does_not_bypass_marker_prompt(self):
        repo = make_git_repo("t-fonce-marker-repo")
        path = write_project("t-fonce-marker.md", autonomy="", vision=False,
                             repo=str(repo), fire_once="2026-06-11",
                             prompt="PROMPT NEEDED. Mint one first.")
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertIn("marker", reason)

    def test_daily_cap(self):
        repo = make_git_repo("t-cap-repo")
        path = write_project("t-cap.md", repo=str(repo))
        today = date.today().isoformat()
        with open(legwork_runner.RUNNER_LOG, "w", encoding="utf-8") as fh:
            for i in range(legwork_runner.DAILY_CAP):
                fh.write(f"{today} 0{i}:00:00  fired t-cap.md in x\n")
        try:
            ok, reason, _ = legwork_runner.assess(path)
            self.assertFalse(ok)
            self.assertIn("daily cap", reason)
        finally:
            legwork_runner.RUNNER_LOG.unlink(missing_ok=True)

    def test_fires_today_ignores_other_days(self):
        with open(legwork_runner.RUNNER_LOG, "w", encoding="utf-8") as fh:
            fh.write("2020-01-01 09:00:00  fired t-old.md in x\n")
        try:
            self.assertEqual(legwork_runner.fires_today("t-old.md"), 0)
        finally:
            legwork_runner.RUNNER_LOG.unlink(missing_ok=True)


class TestTransientCrash(unittest.TestCase):
    """The 529-on-first-call signature re-queues; real failures do not."""

    OVERLOADED = {"type": "result", "is_error": True, "num_turns": 1,
                  "total_cost_usd": 0,
                  "result": 'API Error: 529 {"type":"error","error":'
                            '{"type":"overloaded_error","message":'
                            '"Overloaded"}}'}

    def test_overloaded_first_call_is_transient(self):
        self.assertTrue(legwork_runner.is_transient_crash(self.OVERLOADED))

    def test_work_before_failure_is_not_transient(self):
        worked = dict(self.OVERLOADED, num_turns=14, total_cost_usd=0.42)
        self.assertFalse(legwork_runner.is_transient_crash(worked))

    def test_non_api_error_is_not_transient(self):
        other = dict(self.OVERLOADED, result="Execution error")
        self.assertFalse(legwork_runner.is_transient_crash(other))

    def test_clean_result_is_not_transient(self):
        clean = {"type": "result", "is_error": False, "num_turns": 30,
                 "total_cost_usd": 1.1, "result": "done"}
        self.assertFalse(legwork_runner.is_transient_crash(clean))
        self.assertFalse(legwork_runner.is_transient_crash(None))

    def test_transcript_result_reads_last_result_object(self):
        transcript = TMP / "transient-transcript.jsonl"
        import json as _json
        transcript.write_text(
            '{"type":"system","subtype":"init"}\n'
            + _json.dumps(self.OVERLOADED) + "\n",
            encoding="utf-8")
        obj = legwork_runner.transcript_result(transcript)
        self.assertTrue(legwork_runner.is_transient_crash(obj))

    def test_5xx_and_connection_faults_are_transient(self):
        for text in ("API Error: 503 Service Unavailable",
                     "Internal server error",
                     "Connection reset by peer"):
            obj = dict(self.OVERLOADED, result=text)
            self.assertTrue(legwork_runner.is_transient_crash(obj), text)

    def test_usage_limit_is_not_a_transient_crash(self):
        # Usage limits are handled by the account-level block, not the
        # per-project transient path, even with zero work done.
        obj = dict(self.OVERLOADED, result="Usage limit reached, resets 6:40pm")
        self.assertFalse(legwork_runner.is_transient_crash(obj))


class TestUsageLimit(unittest.TestCase):
    """A usage-limit cutoff defers the whole account until its reset."""

    LIMITED = {"type": "result", "is_error": True, "num_turns": 1,
               "total_cost_usd": 0,
               "result": "You are out of extra usage, resets 6:40pm"}

    def test_parses_reset_clock(self):
        now = datetime(2026, 6, 13, 14, 0, 0)
        limited, reset = legwork_runner.usage_limit_reset(self.LIMITED, now)
        self.assertTrue(limited)
        self.assertEqual(reset, "2026-06-13T18:40:00")

    def test_reset_already_passed_rolls_to_tomorrow(self):
        now = datetime(2026, 6, 13, 20, 0, 0)  # past 6:40pm
        _, reset = legwork_runner.usage_limit_reset(self.LIMITED, now)
        self.assertEqual(reset, "2026-06-14T18:40:00")

    def test_24h_reset_clock(self):
        obj = dict(self.LIMITED, result="usage limit reached, resets at 18:40")
        now = datetime(2026, 6, 13, 14, 0, 0)
        _, reset = legwork_runner.usage_limit_reset(obj, now)
        self.assertEqual(reset, "2026-06-13T18:40:00")

    def test_limited_without_reset(self):
        obj = dict(self.LIMITED, result="usage limit reached")
        limited, reset = legwork_runner.usage_limit_reset(
            obj, datetime(2026, 6, 13, 14, 0, 0))
        self.assertTrue(limited)
        self.assertIsNone(reset)

    def test_not_limited(self):
        obj = dict(self.LIMITED, result="API Error: 529 overloaded")
        limited, reset = legwork_runner.usage_limit_reset(
            obj, datetime(2026, 6, 13, 14, 0, 0))
        self.assertFalse(limited)
        self.assertIsNone(reset)


class TestBackoff(unittest.TestCase):
    """Transient-crash backoff and account usage blocks gate later fires."""

    def setUp(self):
        self.now = datetime(2026, 6, 13, 12, 0, 0)

    def test_backoff_doubles_and_caps(self):
        self.assertEqual(legwork_runner.backoff_seconds(1),
                         legwork_runner.TRANSIENT_BASE)
        self.assertEqual(legwork_runner.backoff_seconds(2),
                         legwork_runner.TRANSIENT_BASE * 2)
        self.assertEqual(legwork_runner.backoff_seconds(99),
                         legwork_runner.TRANSIENT_CAP)

    def test_cooldown_remaining_counts_down(self):
        state = {"transient": {"p": {
            "since": (self.now - timedelta(seconds=60)).isoformat(), "count": 1}}}
        remaining = legwork_runner.transient_cooldown_remaining(
            state, "p", self.now)
        self.assertEqual(remaining, legwork_runner.TRANSIENT_BASE - 60)

    def test_cooldown_expired_is_zero(self):
        state = {"transient": {"p": {
            "since": (self.now - timedelta(hours=3)).isoformat(), "count": 1}}}
        self.assertEqual(
            legwork_runner.transient_cooldown_remaining(state, "p", self.now), 0)
        self.assertEqual(
            legwork_runner.transient_cooldown_remaining({}, "p", self.now), 0)

    def test_usage_block_remaining(self):
        future = (self.now + timedelta(minutes=20)).isoformat()
        state = {"usage_block": {"personal": future}}
        self.assertEqual(
            legwork_runner.usage_block_remaining(state, "personal", self.now),
            20 * 60)
        self.assertEqual(
            legwork_runner.usage_block_remaining(state, "work", self.now), 0)

    def test_update_cooldowns_transient_increments(self):
        state = {}
        out = {"name": "p", "account": "personal", "transient": True,
               "limited": False, "reset": None}
        legwork_runner.update_cooldowns(state, [out], self.now)
        self.assertEqual(state["transient"]["p"]["count"], 1)
        legwork_runner.update_cooldowns(state, [out], self.now)
        self.assertEqual(state["transient"]["p"]["count"], 2)
        legwork_runner.STATE_FILE.unlink(missing_ok=True)

    def test_update_cooldowns_clean_fire_clears(self):
        state = {"transient": {"p": {"since": self.now.isoformat(), "count": 3}}}
        clean = {"name": "p", "account": "personal", "transient": False,
                 "limited": False, "reset": None}
        legwork_runner.update_cooldowns(state, [clean], self.now)
        self.assertNotIn("p", state["transient"])
        legwork_runner.STATE_FILE.unlink(missing_ok=True)

    def test_update_cooldowns_usage_blocks_account(self):
        state = {}
        reset = (self.now + timedelta(hours=4)).isoformat(timespec="seconds")
        out = {"name": "p", "account": "personal", "transient": False,
               "limited": True, "reset": reset}
        legwork_runner.update_cooldowns(state, [out], self.now)
        self.assertEqual(state["usage_block"]["personal"], reset)
        legwork_runner.STATE_FILE.unlink(missing_ok=True)

    def test_update_cooldowns_usage_default_when_no_reset(self):
        state = {}
        out = {"name": "p", "account": "personal", "transient": False,
               "limited": True, "reset": None}
        legwork_runner.update_cooldowns(state, [out], self.now)
        self.assertEqual(
            legwork_runner.usage_block_remaining(state, "personal", self.now),
            legwork_runner.USAGE_BLOCK_DEFAULT)
        legwork_runner.STATE_FILE.unlink(missing_ok=True)

    def test_update_cooldowns_prunes_expired_usage_block(self):
        state = {"usage_block": {
            "personal": (self.now - timedelta(minutes=1)).isoformat()}}
        legwork_runner.update_cooldowns(state, [], self.now)
        self.assertNotIn("personal", state["usage_block"])
        legwork_runner.STATE_FILE.unlink(missing_ok=True)


class TestHelpers(unittest.TestCase):
    def test_days_since(self):
        past = (date.today() - timedelta(days=3)).isoformat()
        self.assertEqual(legwork_runner.days_since(past), 3)
        self.assertIsNone(legwork_runner.days_since("not a date"))

    def test_heartbeat_smoke(self):
        text = legwork_runner.build_heartbeat()
        self.assertIn("Legwork heartbeat", text)
        self.assertIn("Queued", text)

    def test_state_roundtrip(self):
        legwork_runner.save_state({"k": "v"})
        try:
            self.assertEqual(legwork_runner.load_state(), {"k": "v"})
        finally:
            legwork_runner.STATE_FILE.unlink(missing_ok=True)

    def test_porcelain_path(self):
        self.assertEqual(
            legwork_runner.porcelain_path(" M projects/alpha.md"),
            "projects/alpha.md")
        self.assertEqual(
            legwork_runner.porcelain_path("A  suite/legwork_runner.py"),
            "suite/legwork_runner.py")
        self.assertEqual(
            legwork_runner.porcelain_path("R  old.md -> projects/new.md"),
            "projects/new.md")


class TestAuditedFixes(unittest.TestCase):
    """Coverage for the behaviours changed in the audit pass: combined
    directive lines, the reset-date guard, prefix-safe fire counting, the
    cost cap data, frontmatter validation, the rebase tripwire, the
    classify_outcome decision tail, and transient-cooldown pruning."""

    def test_combined_directive_line(self):
        prompt, model, effort, ignored = legwork_runner.extract_directives(
            "Model: opus, Effort: max\n\nDo the task.")
        self.assertEqual((model, effort), ("opus", "max"))
        self.assertEqual(ignored, [])
        self.assertEqual(prompt, "Do the task.")

    def test_reset_date_not_misread_as_clock(self):
        now = datetime(2026, 6, 17, 9, 0, 0)
        limited, reset = legwork_runner.usage_limit_reset(
            {"is_error": True, "result": "usage limit; resets 2026-06-18"}, now)
        self.assertTrue(limited)
        self.assertIsNone(reset, "a date must not parse into a clock time")
        limited2, reset2 = legwork_runner.usage_limit_reset(
            {"is_error": True, "result": "usage limit reached, resets 6:40pm"},
            now)
        self.assertTrue(limited2)
        self.assertTrue(reset2.endswith("18:40:00"))

    def test_fires_today_prefix_safety(self):
        today = date.today().isoformat()
        with open(legwork_runner.RUNNER_LOG, "w", encoding="utf-8") as fh:
            fh.write(f"{today} 09:00:00  fired foo.md in /a\n")
            fh.write(f"{today} 09:05:00  fired foo-bar.md in /b\n")
        try:
            self.assertEqual(legwork_runner.fires_today("foo.md"), 1)
            self.assertEqual(legwork_runner.fires_today("foo-bar.md"), 1)
        finally:
            legwork_runner.RUNNER_LOG.unlink(missing_ok=True)

    def test_cost_today_sums_today_only(self):
        today = date.today().isoformat()
        with open(legwork_runner.RUNNER_LOG, "w", encoding="utf-8") as fh:
            fh.write(f"{today} 09:00:00  completed a.md: exit 0, 5 min, "
                     f"$1.50, 12 turns\n")
            fh.write(f"{today} 10:00:00  completed b.md: exit 0, 2 min, "
                     f"$0.25, 3 turns\n")
            fh.write("2020-01-01 09:00:00  completed c.md: exit 0, 1 min, "
                     "$9.99, 1 turns\n")
            fh.write(f"{today} 11:00:00  fired d.md in /x\n")
        try:
            self.assertAlmostEqual(legwork_runner.cost_today(), 1.75, places=2)
        finally:
            legwork_runner.RUNNER_LOG.unlink(missing_ok=True)

    def test_validate_project_flags_typos(self):
        d = Path(tempfile.mkdtemp(dir=str(TMP)))
        f = d / "bad.md"
        f.write_text("---\nname: Bad\nstatus: paused\nautonomy: yes\n"
                     "wibble: nope\nrepo: none\nupdated: 2026-06-01\n---\n",
                     encoding="utf-8")
        joined = " ".join(legwork_runner.validate_project(f))
        self.assertIn("unknown status 'paused'", joined)
        self.assertIn("autonomy is 'yes'", joined)
        self.assertIn("unknown frontmatter key 'wibble'", joined)

    def test_validate_project_clean(self):
        d = Path(tempfile.mkdtemp(dir=str(TMP)))
        f = d / "ok.md"
        f.write_text("---\nname: Ok\ncategory: personal\nstatus: queued\n"
                     "energy: light\ndescription: fine\nrepo: none\n"
                     "updated: 2026-06-01\nautonomy: loop\n---\n",
                     encoding="utf-8")
        self.assertEqual(legwork_runner.validate_project(f), [])

    def test_rebase_in_progress(self):
        d = Path(tempfile.mkdtemp(dir=str(TMP)))
        (d / ".git").mkdir()
        self.assertFalse(legwork_runner.rebase_in_progress(d))
        (d / ".git" / "rebase-merge").mkdir()
        self.assertTrue(legwork_runner.rebase_in_progress(d))

    def test_classify_outcome_clean(self):
        now = datetime(2026, 6, 17, 9, 0, 0)
        out = legwork_runner.classify_outcome(
            {"is_error": False, "num_turns": 8, "total_cost_usd": 0.4}, now)
        self.assertEqual(
            (out["transient"], out["limited"], out["requeue"]),
            (False, False, False))

    def test_classify_outcome_transient_requeues(self):
        now = datetime(2026, 6, 17, 9, 0, 0)
        out = legwork_runner.classify_outcome(
            {"is_error": True, "num_turns": 1, "total_cost_usd": 0,
             "result": "API Error: 529 overloaded"}, now)
        self.assertTrue(out["transient"])
        self.assertTrue(out["requeue"])
        self.assertFalse(out["limited"])

    def test_classify_outcome_usage_before_work_requeues(self):
        now = datetime(2026, 6, 17, 9, 0, 0)
        out = legwork_runner.classify_outcome(
            {"is_error": True, "num_turns": 1, "total_cost_usd": 0,
             "result": "usage limit reached, resets 6:40pm"}, now)
        self.assertTrue(out["limited"])
        self.assertTrue(out["requeue"])
        self.assertFalse(out["transient"])
        self.assertTrue(out["reset"].endswith("18:40:00"))

    def test_classify_outcome_usage_mid_work_does_not_requeue(self):
        # The composition the unit tests could not reach before fire()'s tail
        # was extracted: a usage limit that hit after real work defers the
        # account but keeps the output for review (requeue must be False).
        now = datetime(2026, 6, 17, 9, 0, 0)
        out = legwork_runner.classify_outcome(
            {"is_error": True, "num_turns": 9, "total_cost_usd": 0.7,
             "result": "usage limit reached"}, now)
        self.assertTrue(out["limited"])
        self.assertFalse(out["requeue"])
        self.assertFalse(out["transient"])

    def test_update_cooldowns_prunes_expired_transient(self):
        now = datetime(2026, 6, 17, 9, 0, 0)
        old = (now - timedelta(days=1)).isoformat()
        state = {"transient": {"stale": {"since": old, "count": 1}}}
        try:
            legwork_runner.update_cooldowns(state, [], now)
            self.assertNotIn("stale", state["transient"])
        finally:
            legwork_runner.STATE_FILE.unlink(missing_ok=True)

    def test_guard_settings_use_absolute_deny_paths(self):
        import json
        legwork_runner.write_guard_settings()
        try:
            deny = json.loads(legwork_runner.GUARD_SETTINGS.read_text(
                encoding="utf-8"))["permissions"]["deny"]
        finally:
            legwork_runner.GUARD_SETTINGS.unlink(missing_ok=True)
        # Claude Code reads a single leading "/" as project-relative; an
        # absolute deny must be "//" or it silently matches nothing. A real
        # session confirmed "//" blocks a control-plane write; pin it here so
        # the format cannot regress unnoticed.
        self.assertTrue(deny)
        for rule in deny:
            inside = rule.split("(", 1)[1].rstrip(")")
            self.assertTrue(inside.startswith("//"),
                            f"deny rule must be an absolute // path: {rule}")
        # The whole control plane is covered: core/ (hooks, dashboard
        # builder), suite/ (runner, reviewer, n8n) and scripts/ (installer).
        for sub in ("/core/**", "/suite/**", "/scripts/**"):
            self.assertTrue(any(r.startswith(("Edit(", "Write("))
                                and sub in r for r in deny), sub)


class TestLocalReview(unittest.TestCase):
    """The pure pieces of the local reviewer: verdict parsing, the file
    write-back for each verdict, the decision-brief rendering and the alert
    formats. No git, no network: apply_verdict is text in, text out."""

    NOW = datetime(2026, 6, 18, 10, 0, 0)

    def project_text(self, status="review", prompt="Read PROJECT.md.\n\n"
                     "Task: the original task.\n\nDone when: done.\n\n"
                     "Final step: run /wrap to update the tracker and mint "
                     "the next prompt."):
        return ("---\nname: demo\ncategory: personal\n"
                f"status: {status}\nenergy: light\ndescription: test\n"
                "repo: none\nupdated: 2026-06-01\nautonomy: loop\n---\n\n"
                "## Vision\n\n- North star: a thing.\n\n"
                "## Next prompt\n\n```text\n" + prompt + "\n```\n\n"
                "## Log\n\n- 2026-06-01: created.\n")

    def test_parse_verdict_plain_json(self):
        v = legwork_review.parse_verdict(
            '{"verdict":"pass","confidence":0.9,"summary":"looks good"}')
        self.assertEqual(v["verdict"], "pass")
        self.assertEqual(v["summary"], "looks good")

    def test_parse_verdict_strips_fences(self):
        v = legwork_review.parse_verdict(
            'Sure:\n```json\n{"verdict":"revise","summary":"x"}\n```\n')
        self.assertEqual(v["verdict"], "revise")

    def test_parse_verdict_recovers_from_prose(self):
        v = legwork_review.parse_verdict(
            'Here is my review. {"verdict":"escalate","summary":"y"} Done.')
        self.assertEqual(v["verdict"], "escalate")

    def test_parse_verdict_rejects_garbage(self):
        self.assertIsNone(legwork_review.parse_verdict("not json at all"))
        self.assertIsNone(legwork_review.parse_verdict(
            '{"verdict":"banana"}'))
        self.assertIsNone(legwork_review.parse_verdict(""))

    def test_build_evidence_text_shape(self):
        text = legwork_review.build_evidence_text({
            "repo": "demo", "end_reason": "normal",
            "tracker_entry": "the prompt"})
        self.assertIn("repo: demo", text)
        self.assertIn("end_reason: normal", text)
        self.assertIn("the prompt", text)
        # Missing fields fall back to the n8n placeholders, not blanks.
        self.assertIn("(none recorded)", text)
        self.assertIn("(empty)", text)

    def test_apply_pass_requeues_and_keeps_prompt(self):
        out = legwork_review.apply_verdict(
            self.project_text(), {"verdict": "pass", "summary": "all good"},
            self.NOW)
        text, status, detail = out
        self.assertEqual(status, "queued")
        self.assertIn("status: queued", text)
        self.assertIn("the original task", text, "pass keeps the wrapped prompt")
        self.assertIn("Reviewer passed: all good", text)
        self.assertIn("updated: 2026-06-18", text)

    def test_apply_revise_installs_fix_prompt(self):
        out = legwork_review.apply_verdict(
            self.project_text(),
            {"verdict": "revise", "summary": "off by one",
             "fix_prompt": "Read PROJECT.md.\n\nTask: fix the off-by-one.\n\n"
                           "Done when: tests pass.\n\nFinal step: run /wrap."},
            self.NOW)
        text, status, _ = out
        self.assertEqual(status, "queued")
        self.assertIn("fix the off-by-one", text)
        self.assertNotIn("the original task", text,
                         "revise replaces the wrapped prompt")
        self.assertIn("Reviewer revise: off by one", text)

    def test_apply_revise_strips_fences_from_fix_prompt(self):
        out = legwork_review.apply_verdict(
            self.project_text(),
            {"verdict": "revise", "summary": "s",
             "fix_prompt": "```text\nTask: do it.\n```"}, self.NOW)
        text, _, _ = out
        # The fix prompt's own fence must not nest inside the Next prompt fence.
        self.assertIn("Task: do it.", text)
        self.assertNotIn("```text\nTask: do it.\n```\n```", text)

    def test_apply_escalate_writes_brief_and_flips_status(self):
        out = legwork_review.apply_verdict(
            self.project_text(),
            {"verdict": "escalate", "summary": "needs a call",
             "decision_brief": {
                 "attempted": "tried X", "uncertain": "whether Y",
                 "options": ["A. do Y", "B. skip Y"],
                 "recommendation": "A"}}, self.NOW)
        text, status, _ = out
        self.assertEqual(status, "escalated")
        self.assertIn("status: escalated", text)
        self.assertIn("DECISION NEEDED", text)
        self.assertIn("A. do Y", text)
        self.assertIn("Reviewer escalated: needs a call", text)
        # The written prompt is a not-a-prompt marker, so even if the status
        # were later flipped to queued the runner would refuse to fire it.
        prompt = PROMPT_FROM(text)
        self.assertTrue(prompt.startswith(legwork_runner.NOT_A_PROMPT))

    def test_apply_skips_terminal_status(self):
        for status in ("done", "icebox"):
            self.assertIsNone(legwork_review.apply_verdict(
                self.project_text(status=status),
                {"verdict": "pass", "summary": "x"}, self.NOW),
                f"{status} must not be resurrected")

    def test_apply_rejects_unknown_verdict(self):
        self.assertIsNone(legwork_review.apply_verdict(
            self.project_text(), {"verdict": "maybe"}, self.NOW))

    def test_render_decision_brief_format(self):
        rendered = legwork_review.render_decision_brief(
            {"attempted": "a", "uncertain": "u",
             "options": ["A. one", "B. two"], "recommendation": "B"})
        self.assertTrue(rendered.startswith("DECISION NEEDED"))
        self.assertIn("Attempted: a", rendered)
        self.assertIn("Uncertain: u", rendered)
        self.assertIn("A. one", rendered)
        self.assertIn("Recommendation: B", rendered)

    def test_verdict_alert_formats(self):
        self.assertIn("PASS  demo", legwork_review.verdict_alert(
            {"verdict": "pass", "summary": "s", "confidence": 0.9}, "demo"))
        revise = legwork_review.verdict_alert(
            {"verdict": "revise", "summary": "s", "reasons": ["r1", "r2"],
             "fix_prompt": "do it"}, "demo")
        self.assertIn("REVISE  demo", revise)
        self.assertIn("Why: r1; r2", revise)
        self.assertIn("Fix prompt", revise)
        esc = legwork_review.verdict_alert(
            {"verdict": "escalate", "summary": "s", "decision_brief": {
                "attempted": "a", "options": ["A. x"], "recommendation": "A"}},
            "demo")
        self.assertIn("NEEDS YOU  demo", esc)
        self.assertIn("Reply with a letter.", esc)

    def test_review_orchestration_with_stubbed_call(self):
        original = legwork_review.call_claude
        legwork_review.call_claude = lambda *a, **k: \
            '{"verdict":"pass","summary":"fine"}'
        try:
            v = legwork_review.review({"repo": "demo"}, "sonnet", "claude")
        finally:
            legwork_review.call_claude = original
        self.assertEqual(v["verdict"], "pass")

    def test_review_returns_none_when_call_fails(self):
        original = legwork_review.call_claude
        legwork_review.call_claude = lambda *a, **k: None
        try:
            self.assertIsNone(
                legwork_review.review({"repo": "demo"}, "sonnet", "claude"))
        finally:
            legwork_review.call_claude = original

    def test_revise_without_fix_prompt_is_refused(self):
        # A revise with no usable fix prompt must NOT overwrite the wrapped
        # prompt with an empty block (which would strand the project); it is
        # refused so the runner parks it for a human.
        for fp in ("", "   ", "```text\n```", None):
            v = {"verdict": "revise", "summary": "s", "fix_prompt": fp}
            self.assertFalse(legwork_review.is_actionable(v), repr(fp))
            self.assertIsNone(
                legwork_review.apply_verdict(self.project_text(), v, self.NOW),
                repr(fp))
        # A real fix prompt is actionable and applies.
        good = {"verdict": "revise", "summary": "s", "fix_prompt": "Task: go."}
        self.assertTrue(legwork_review.is_actionable(good))
        self.assertIsNotNone(
            legwork_review.apply_verdict(self.project_text(), good, self.NOW))

    def test_pass_and_escalate_always_actionable(self):
        self.assertTrue(legwork_review.is_actionable({"verdict": "pass"}))
        self.assertTrue(legwork_review.is_actionable(
            {"verdict": "escalate", "decision_brief": {}}))

    def test_revise_fix_prompt_with_embedded_fence_does_not_truncate(self):
        # A fix prompt that itself contains a fenced code block must flatten,
        # not nest inside the ```text wrapper and truncate on read-back.
        fix = ("Read PROJECT.md.\n\nTask: make the signature:\n\n"
               "```python\ndef parse(row):\n    ...\n```\n\n"
               "Done when: tests pass.\n\nFinal step: run /wrap.")
        text, _, _ = legwork_review.apply_verdict(
            self.project_text(), {"verdict": "revise", "summary": "s",
                                  "fix_prompt": fix}, self.NOW)
        readback = PROMPT_FROM(text)
        # The whole prompt survives the round-trip: the tail (Done when / wrap)
        # is not lost, and no stray fence remains to break the block.
        self.assertIn("Done when: tests pass.", readback)
        self.assertIn("Final step: run /wrap.", readback)
        self.assertNotIn("```", readback)

    def test_escalate_brief_with_embedded_fence_does_not_truncate(self):
        text, _, _ = legwork_review.apply_verdict(
            self.project_text(),
            {"verdict": "escalate", "summary": "s", "decision_brief": {
                "attempted": "ran ```sh\nrm -rf x\n``` by mistake",
                "uncertain": "u", "options": ["A. x", "B. y"],
                "recommendation": "A"}}, self.NOW)
        readback = PROMPT_FROM(text)
        self.assertIn("DECISION NEEDED", readback)
        self.assertIn("Recommendation: A", readback)
        self.assertNotIn("```", readback)

    def test_apply_appends_next_prompt_when_section_missing(self):
        # _replace_next_prompt's append branch: a file with no ## Next prompt.
        text = ("---\nname: d\nstatus: review\nupdated: 2026-06-01\n---\n\n"
                "## Log\n\n- 2026-06-01: x.\n")
        out = legwork_review.apply_verdict(
            text, {"verdict": "revise", "summary": "s",
                   "fix_prompt": "Task: do it."}, self.NOW)
        self.assertIsNotNone(out)
        self.assertIn("## Next prompt", out[0])
        self.assertIn("Task: do it.", PROMPT_FROM(out[0]))

    def test_build_evidence_text_populated_and_ordered(self):
        text = legwork_review.build_evidence_text({
            "repo": "demo", "branch": "main", "last_commit": "abc x",
            "diff_stat": " a | 2 +-", "session_commits": "abc x",
            "uncommitted_files": "0", "uncommitted_list": "",
            "test_output": "RUNNER: re-queued after API error",
            "tracker_entry": "Task: t.", "end_reason": "runner-recovery"})
        self.assertIn("end_reason: runner-recovery", text)
        self.assertIn("RUNNER: re-queued after API error", text)
        # Order: repo header precedes the diff which precedes the tracker entry.
        self.assertLess(text.index("repo: demo"), text.index("diff_stat:"))
        self.assertLess(text.index("diff_stat:"), text.index("Task: t."))

    def test_call_claude_envelope_parsing(self):
        import types
        orig = legwork_review.subprocess.run

        def stub(stdout, returncode=0):
            return lambda argv, **kw: types.SimpleNamespace(
                returncode=returncode, stdout=stdout, stderr="")
        cases = [
            ('{"result":"THE VERDICT","is_error":false}', 0, "THE VERDICT"),
            ('{"result":"x","is_error":true}', 0, None),
            ('anything', 1, None),                 # non-zero exit
            ('not json at all', 0, "not json at all"),  # raw fallback
        ]
        try:
            for out, rc, expected in cases:
                legwork_review.subprocess.run = stub(out, rc)
                self.assertEqual(
                    legwork_review.call_claude("p", "m", "claude"), expected,
                    f"{out!r} rc={rc}")
            # A subprocess failure (e.g. binary missing) yields None.
            def boom(*a, **k):
                raise OSError("no claude")
            legwork_review.subprocess.run = boom
            self.assertIsNone(legwork_review.call_claude("p", "m", "claude"))
        finally:
            legwork_review.subprocess.run = orig


def PROMPT_FROM(text):
    """The first fenced block under ## Next prompt, via the shared regex."""
    import legwork_common
    m = legwork_common.PROMPT_RE.search(text)
    return m.group(1).strip() if m else ""


class TestConcurrentRunner(unittest.TestCase):
    """Claim and wrap race paths: parallel claims serialise behind
    WRITE_LOCK, remote movement (n8n write-back, parallel wraps) is rebased
    over, and one tick fires every eligible project except repo-sharers.
    The sandbox legwork dir is a real git clone with a bare origin standing
    in for the GitHub remote (see _init_sandbox_repo)."""

    def setUp(self):
        # Belt and braces: nothing in these tests may hit the network.
        self._send_alert = legwork_runner.send_alert
        legwork_runner.send_alert = lambda text: True

    def tearDown(self):
        legwork_runner.send_alert = self._send_alert

    def run_tick(self, fire_stub=None, keep_state=False):
        """One tick with fire() stubbed out and the daily heartbeat marked
        already sent; returns the file names the tick tried to fire. State
        is removed afterwards unless keep_state (for multi-tick tests)."""
        state = legwork_runner.load_state()
        state["last_heartbeat"] = date.today().isoformat()
        legwork_runner.save_state(state)
        fired = []
        original = legwork_runner.fire
        legwork_runner.fire = fire_stub or (
            lambda project: fired.append(project["file"].name))
        try:
            legwork_runner.tick()
        finally:
            legwork_runner.fire = original
            if not keep_state:
                legwork_runner.STATE_FILE.unlink(missing_ok=True)
        return fired

    def test_parallel_claims_both_publish(self):
        write_project("c-claim-a.md", repo=str(make_git_repo("c-claim-a-repo")))
        write_project("c-claim-b.md", repo=str(make_git_repo("c-claim-b-repo")))
        commit_and_push("add claim race projects")
        results = {}

        def do_claim(fname):
            path = SANDBOX / "projects" / fname
            results[fname] = legwork_runner.claim({"file": path})

        threads = [threading.Thread(target=do_claim, args=(f,))
                   for f in ("c-claim-a.md", "c-claim-b.md")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(results["c-claim-a.md"], "claim a should publish")
        self.assertTrue(results["c-claim-b.md"], "claim b should publish")
        self.assertNotEqual(results["c-claim-a.md"], results["c-claim-b.md"])
        for fname in ("c-claim-a.md", "c-claim-b.md"):
            self.assertIn("status: running",
                          origin_file(f"projects/{fname}"))

    def test_claim_publishes_over_remote_movement(self):
        path = write_project("c-race.md",
                             repo=str(make_git_repo("c-race-repo")))
        commit_and_push("add remote race project")
        remote_commit("reviewer-note.txt", "verdict",
                           "n8n: verdict appended")
        sha = legwork_runner.claim({"file": path})
        self.assertTrue(sha, "claim should land despite the moved remote")
        subjects = origin_subjects()
        self.assertIn("legwork: runner fires c-race", subjects)
        self.assertIn("n8n: verdict appended", subjects)

    def test_claim_dropped_when_remote_flips_status(self):
        path = write_project("c-flip.md",
                             repo=str(make_git_repo("c-flip-repo")))
        commit_and_push("add flip race project")
        flipped = path.read_text(encoding="utf-8").replace(
            "status: queued", "status: escalated")
        remote_commit("projects/c-flip.md", flipped,
                           "n8n: escalate c-flip")
        sha = legwork_runner.claim({"file": path})
        self.assertIsNone(sha, "a status moved off queued drops the claim")
        self.assertIn("status: escalated",
                      path.read_text(encoding="utf-8"))

    def test_claim_consumes_fire_once(self):
        path = write_project("c-fonce.md", autonomy="", vision=False,
                             repo=str(make_git_repo("c-fonce-repo")),
                             fire_once="2026-06-11")
        commit_and_push("add fire_once project")
        sha = legwork_runner.claim({"file": path})
        self.assertTrue(sha, "fire_once claim should publish")
        published = origin_file("projects/c-fonce.md")
        self.assertIn("status: running", published)
        self.assertNotIn("fire_once", published,
                         "the claim must consume the one-shot consent")

    def test_claim_flips_capitalized_status(self):
        # assess() lowercases the status check, so a hand-typed "Queued"
        # is eligible; the claim must flip it too or the project assesses
        # eligible every tick and logs "claim dropped" forever.
        path = write_project("c-caps.md", status="Queued",
                             repo=str(make_git_repo("c-caps-repo")))
        commit_and_push("add capitalized status project")
        sha = legwork_runner.claim({"file": path})
        self.assertTrue(sha, "a hand-typed 'Queued' must still claim")
        self.assertIn("status: running",
                      origin_file("projects/c-caps.md"))

    def test_repair_transient_restores_fire_once(self):
        # The reviewer's repro for the lost one-shot: claim consumes
        # fire_once, the session dies on a transient error, and the requeue
        # must hand the consent back or the promised retry can never fire.
        path = write_project("c-fonce-req.md", autonomy="", vision=False,
                             repo=str(make_git_repo("c-fonce-req-repo")),
                             fire_once="2026-07-03")
        commit_and_push("add fire_once requeue project")
        ok, _, details = legwork_runner.assess(path)
        self.assertTrue(ok)
        self.assertEqual(details["fire_once"], "2026-07-03")
        self.assertTrue(legwork_runner.claim(details))
        self.assertNotIn("fire_once", path.read_text(encoding="utf-8"))
        legwork_runner.repair_unwrapped(details, exit_code=1, minutes=1,
                                        transient=True)
        text = path.read_text(encoding="utf-8")
        self.assertIn("status: queued", text)
        self.assertIn("fire_once: 2026-07-03", text)
        ok, reason, _ = legwork_runner.assess(path)
        self.assertTrue(ok, f"restored one-shot must be eligible ({reason})")

    def test_repair_flips_capitalized_running(self):
        path = write_project("c-caps-run.md", status="Running",
                             repo=str(make_git_repo("c-caps-run-repo")))
        commit_and_push("add capitalized running project")
        detail = legwork_runner.repair_unwrapped({"file": path},
                                                 exit_code=1, minutes=2)
        self.assertIsNotNone(detail)
        self.assertIn("status: review", path.read_text(encoding="utf-8"))

    def test_repair_without_log_heading_appends_one(self):
        path = write_project("c-nolog.md", status="running",
                             repo=str(make_git_repo("c-nolog-repo")))
        text = path.read_text(encoding="utf-8")
        path.write_text(text.split("## Log")[0], encoding="utf-8")
        commit_and_push("add no-log project")
        legwork_runner.repair_unwrapped({"file": path}, exit_code=1,
                                        minutes=2)
        text = path.read_text(encoding="utf-8")
        self.assertIn("## Log", text)
        self.assertIn("Runner: autonomous session exited without wrapping",
                      text)

    def test_fire_crash_after_claim_repairs_running_status(self):
        # A crash anywhere after the claim published status: running must
        # not strand it until the daily heartbeat: fire() repairs before
        # re-raising into fire_thread's net.
        path = write_project("c-crash.md",
                             repo=str(make_git_repo("c-crash-repo")))
        commit_and_push("add crash project")
        ok, _, details = legwork_runner.assess(path)
        self.assertTrue(ok)
        orig_claimed = legwork_runner.fire_claimed
        orig_find = legwork_runner.find_claude

        def boom(*args, **kwargs):
            raise RuntimeError("post-claim crash")

        legwork_runner.fire_claimed = boom
        legwork_runner.find_claude = lambda: "claude"
        try:
            outcome = legwork_runner.fire_thread(details)
        finally:
            legwork_runner.fire_claimed = orig_claimed
            legwork_runner.find_claude = orig_find
        self.assertIsNone(outcome)
        text = path.read_text(encoding="utf-8")
        self.assertIn("status: review", text)
        self.assertIn("exited without wrapping", text)

    def test_remote_pause_arriving_with_the_pull_stops_the_tick(self):
        write_project("c-paused.md",
                      repo=str(make_git_repo("c-paused-repo")))
        commit_and_push("add project behind remote pause")
        remote_commit(".runner-pause-remote", "Paused via Telegram\n",
                      "n8n: pause the runner")
        try:
            fired = self.run_tick()
        finally:
            # Lift the pause for the tests that follow.
            (SANDBOX / ".runner-pause-remote").unlink(missing_ok=True)
            commit_and_push("lift remote pause")
        self.assertEqual(fired, [], "a freshly pulled pause must stop firing")

    def test_push_with_rebase_recovers(self):
        marker = SANDBOX / "c-local-note.txt"
        marker.write_text("local work", encoding="utf-8")
        subprocess.run(["git", "add", str(marker)], cwd=SANDBOX, check=True)
        subprocess.run(["git", *GIT_ID, "commit", "-q", "-m",
                        "local: unpushed work"], cwd=SANDBOX, check=True)
        remote_commit("c-remote-note.txt", "remote work",
                      "remote: moved first")
        self.assertTrue(legwork_runner.push_with_rebase(SANDBOX))
        subjects = origin_subjects()
        self.assertIn("local: unpushed work", subjects)
        self.assertIn("remote: moved first", subjects)

    def test_push_with_rebase_aborts_a_real_conflict(self):
        # A same-line conflict on a tracked file: the rebase cannot apply,
        # so push_with_rebase must abort it and report failure rather than
        # leave the control-plane repo wedged mid-rebase, which would block
        # every future tick.
        marker = SANDBOX / "c-conflict.txt"
        marker.write_text("base\n", encoding="utf-8")
        commit_and_push("add conflict base")
        marker.write_text("local change\n", encoding="utf-8")
        subprocess.run(["git", "add", str(marker)], cwd=SANDBOX, check=True)
        subprocess.run(["git", *GIT_ID, "commit", "-q", "-m",
                        "local: conflicting work"], cwd=SANDBOX, check=True)
        remote_commit("c-conflict.txt", "remote change\n",
                      "remote: conflicting work")
        try:
            self.assertFalse(legwork_runner.push_with_rebase(SANDBOX))
            self.assertFalse(
                legwork_runner.rebase_in_progress(SANDBOX),
                "a failed rebase must be aborted, not left half-applied")
            self.assertIn(
                "aborted a conflicted rebase",
                legwork_runner.RUNNER_LOG.read_text(encoding="utf-8"))
        finally:
            # Drop the unpushable local commit so later tests see a clean,
            # in-sync repo again.
            subprocess.run(["git", "fetch", "-q", "origin"], cwd=SANDBOX,
                           check=True)
            subprocess.run(["git", "reset", "-q", "--hard", "origin/main"],
                           cwd=SANDBOX, check=True)

    def test_tick_cost_cap_reached_fires_nothing_and_alerts_once(self):
        # The spend guard's gate in tick(), not just cost_today()'s sum:
        # over the cap nothing fires, and the alert goes out once per day,
        # not once per five-minute tick.
        write_project("c-costcap.md",
                      repo=str(make_git_repo("c-costcap-repo")))
        commit_and_push("add cost cap project")
        today = date.today().isoformat()
        with open(legwork_runner.RUNNER_LOG, "w", encoding="utf-8") as fh:
            fh.write(f"{today} 09:00:00  completed a.md: exit 0, 5 min, "
                     f"$4.00, 12 turns\n")
            fh.write(f"{today} 10:00:00  completed b.md: exit 0, 2 min, "
                     f"$1.50, 3 turns\n")
        alerts = []
        legwork_runner.send_alert = lambda text: alerts.append(text) or True
        orig_cap = legwork_runner.DAILY_COST_CAP
        legwork_runner.DAILY_COST_CAP = 5.0
        try:
            fired = self.run_tick(keep_state=True)
            self.assertEqual(fired, [], "over the cap, nothing may fire")
            self.assertEqual(len(alerts), 1)
            self.assertIn("cost cap", alerts[0])
            fired = self.run_tick()  # second tick, same day
            self.assertEqual(fired, [])
            self.assertEqual(len(alerts), 1,
                             "the cap alerts once per day, not per tick")
        finally:
            legwork_runner.DAILY_COST_CAP = orig_cap
            legwork_runner.RUNNER_LOG.unlink(missing_ok=True)
            legwork_runner.STATE_FILE.unlink(missing_ok=True)

    def test_tick_under_the_cost_cap_still_fires(self):
        write_project("c-costok.md",
                      repo=str(make_git_repo("c-costok-repo")))
        commit_and_push("add under-cap project")
        today = date.today().isoformat()
        with open(legwork_runner.RUNNER_LOG, "w", encoding="utf-8") as fh:
            fh.write(f"{today} 09:00:00  completed a.md: exit 0, 5 min, "
                     f"$1.00, 2 turns\n")
        orig_cap = legwork_runner.DAILY_COST_CAP
        legwork_runner.DAILY_COST_CAP = 50.0
        try:
            fired = self.run_tick()
            self.assertIn("c-costok.md", fired,
                          "under the cap, eligible projects still fire")
        finally:
            legwork_runner.DAILY_COST_CAP = orig_cap
            legwork_runner.RUNNER_LOG.unlink(missing_ok=True)

    def test_repair_flips_unwrapped_session_and_pushes(self):
        path = write_project("c-repair.md", status="running",
                             repo=str(make_git_repo("c-repair-repo")))
        commit_and_push("add unwrapped session project")
        detail = legwork_runner.repair_unwrapped(
            {"file": path}, exit_code=1, minutes=7)
        self.assertIn("exit 1", detail)
        text = path.read_text(encoding="utf-8")
        self.assertIn("status: review", text)
        self.assertIn("Runner: autonomous session exited without wrapping",
                      text)
        self.assertIn("status: review",
                      origin_file("projects/c-repair.md"))

    def test_repair_transient_requeues_instead_of_review(self):
        path = write_project("c-transient.md", status="running",
                             repo=str(make_git_repo("c-transient-repo")))
        commit_and_push("add transient crash project")
        detail = legwork_runner.repair_unwrapped(
            {"file": path}, exit_code=1, minutes=1, transient=True)
        self.assertIn("re-queued", detail)
        text = path.read_text(encoding="utf-8")
        self.assertIn("status: queued", text)
        self.assertIn("transient API error", text)
        self.assertIn("status: queued",
                      origin_file("projects/c-transient.md"))

    def test_repair_skips_session_that_wrapped(self):
        path = write_project("c-wrapped.md", status="review",
                             repo=str(make_git_repo("c-wrapped-repo")))
        commit_and_push("add wrapped session project")
        self.assertIsNone(legwork_runner.repair_unwrapped(
            {"file": path}, exit_code=0, minutes=3))

    def test_repair_recognizes_wrapped_session_left_running(self):
        # The session wrapped (committed a tracker edit with its own honest
        # message) but forgot to flip status off running. The runner must
        # move it to review without claiming it exited without wrapping.
        path = write_project("c-wrap-running.md", status="running",
                             repo=str(make_git_repo("c-wrap-running-repo")))
        commit_and_push("add wrapped-but-running project")
        claim_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=SANDBOX,
            capture_output=True, text=True).stdout.strip()
        path.write_text(path.read_text(encoding="utf-8") +
                        "\n- 2026-06-13: did the work, minted next prompt.\n",
                        encoding="utf-8")
        subprocess.run(["git", "add", str(path)], cwd=SANDBOX, check=True)
        subprocess.run(["git", *GIT_ID, "commit", "-q", "-m",
                        "CWrapRunning: log work, mint next prompt"],
                       cwd=SANDBOX, check=True)
        subprocess.run(["git", "push", "-q"], cwd=SANDBOX, check=True)
        detail = legwork_runner.repair_unwrapped(
            {"file": path}, exit_code=0, minutes=6, claim_head=claim_head)
        self.assertIn("wrapped but left status running", detail)
        self.assertNotIn("exited without wrapping", detail)
        text = path.read_text(encoding="utf-8")
        self.assertIn("status: review", text)
        self.assertNotIn("exited without wrapping", text)
        self.assertIn("status: review",
                      origin_file("projects/c-wrap-running.md"))

    def test_fire_all_runs_sessions_in_parallel(self):
        barrier = threading.Barrier(2)
        passed = []

        def stub_fire(project):
            # Only concurrent sessions both reach the barrier; a serial
            # runner deadlocks here and times out instead.
            try:
                barrier.wait(timeout=10)
                passed.append(project["file"].name)
            except threading.BrokenBarrierError:
                pass

        original = legwork_runner.fire
        legwork_runner.fire = stub_fire
        try:
            legwork_runner.fire_all([
                {"file": SANDBOX / "projects" / "c-par-a.md"},
                {"file": SANDBOX / "projects" / "c-par-b.md"},
            ])
        finally:
            legwork_runner.fire = original
        self.assertEqual(sorted(passed), ["c-par-a.md", "c-par-b.md"])

    def test_tick_auto_commits_tracker_only_edit_and_fires(self):
        # A manual work-account wrap leaves a projects/ file dirty with no
        # hook to commit it. The runner must commit that itself and still
        # fire the same tick, not stall.
        repo = make_git_repo("c-autocommit-repo")
        write_project("c-autocommit.md", repo=str(repo), updated="2026-06-05")
        commit_and_push("add autocommit project")
        p = SANDBOX / "projects" / "c-autocommit.md"
        p.write_text(p.read_text(encoding="utf-8") +
                     "\n- 2026-06-14: manual wrap, never committed.\n",
                     encoding="utf-8")
        fired = self.run_tick()
        clean = subprocess.run(["git", "status", "--porcelain"], cwd=SANDBOX,
                               capture_output=True, text=True).stdout.strip()
        self.assertEqual(clean, "", "tracker edit should have been committed")
        self.assertIn("runner auto-commits tracker edits",
                      origin_subjects())
        self.assertIn("c-autocommit.md", fired,
                      "project should fire the same tick it was committed")

    def test_tick_auto_commit_leaves_untracked_scratch_alone(self):
        # The auto-commit stages tracked tracker edits only (-u): an
        # untracked scratch file sitting in projects/ must not be swept
        # into the runner's commit.
        repo = make_git_repo("c-scratch-repo")
        write_project("c-scratch.md", repo=str(repo), updated="2026-06-06")
        commit_and_push("add scratch project")
        p = SANDBOX / "projects" / "c-scratch.md"
        p.write_text(p.read_text(encoding="utf-8") +
                     "\n- 2026-06-15: manual wrap, never committed.\n",
                     encoding="utf-8")
        scratch = SANDBOX / "projects" / "c-scratch-note.txt"
        scratch.write_text("do not commit me\n", encoding="utf-8")
        self.run_tick(fire_stub=lambda project: None)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=SANDBOX,
                                capture_output=True, text=True).stdout
        self.assertIn("?? projects/c-scratch-note.txt", status,
                      "the scratch file must stay untracked")
        scratch.unlink()
        self.assertEqual(
            origin_file("projects/c-scratch-note.txt"), "",
            "the scratch file must not reach the remote")
        self.assertIn("manual wrap, never committed",
                      origin_file("projects/c-scratch.md"),
                      "the tracked tracker edit itself is still committed")

    def test_tick_blocks_on_dirty_file_outside_projects(self):
        # A dirty tracked file outside projects/ must still stall the tick;
        # only tracker-only edits are auto-committed.
        (SANDBOX / "suite").mkdir(exist_ok=True)
        f = SANDBOX / "suite" / "c-dirty.txt"
        f.write_text("v1\n", encoding="utf-8")
        commit_and_push("add tracked non-tracker file")
        f.write_text("v2 uncommitted\n", encoding="utf-8")
        try:
            fired = self.run_tick()
        finally:
            # Revert so later tests are not blocked by this dirty file.
            f.write_text("v1\n", encoding="utf-8")
            commit_and_push("revert dirty non-tracker file")
        self.assertEqual(fired, [],
                         "a dirty file outside projects/ must stall firing")

    def test_tick_fires_every_eligible_and_defers_shared_repo(self):
        shared = make_git_repo("c-shared-repo")
        write_project("c-tick-a.md", repo=str(shared), updated="2026-06-01")
        write_project("c-tick-b.md", repo=str(shared), updated="2026-06-02")
        write_project("c-tick-c.md", repo=str(make_git_repo("c-solo-repo")),
                      updated="2026-06-03")
        commit_and_push("add tick fan-out projects")
        fired = self.run_tick()
        self.assertIn("c-tick-a.md", fired)
        self.assertIn("c-tick-c.md", fired)
        self.assertNotIn("c-tick-b.md", fired,
                         "repo-sharing project defers to a later tick")


    def test_audit_alerts_on_control_plane_edit(self):
        # The control-plane tripwire: a session window that commits outside
        # projects/ and dashboard/ (editing the runner, hooks or reviewer)
        # must raise an alert.
        claim_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=SANDBOX,
            capture_output=True, text=True).stdout.strip()
        calls = []
        legwork_runner.send_alert = lambda text: calls.append(text) or True
        evil = SANDBOX / "suite" / "c-evil.py"
        evil.parent.mkdir(exist_ok=True)
        evil.write_text("# tampered\n", encoding="utf-8")
        # Stage only the tampered file: the reset below rolls this commit
        # back, and a sweeping add -A would take other tests' still-untracked
        # project files down with it.
        subprocess.run(["git", "add", str(evil)], cwd=SANDBOX, check=True)
        subprocess.run(["git", *GIT_ID, "commit", "-q", "-m", "evil edit"],
                       cwd=SANDBOX, check=True)
        try:
            legwork_runner.audit_session_window(
                {"file": SANDBOX / "projects" / "c-audit.md"}, claim_head)
            self.assertTrue(calls, "control-plane edit should alert")
            self.assertIn("suite/c-evil.py", calls[0])
        finally:
            subprocess.run(["git", "reset", "-q", "--hard", claim_head],
                           cwd=SANDBOX, check=True)

    def test_audit_silent_on_tracker_only_edit(self):
        claim_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=SANDBOX,
            capture_output=True, text=True).stdout.strip()
        calls = []
        legwork_runner.send_alert = lambda text: calls.append(text) or True
        tracker = SANDBOX / "projects" / "c-audit-ok.md"
        tracker.write_text("---\nname: ok\n---\n", encoding="utf-8")
        subprocess.run(["git", "add", str(tracker)], cwd=SANDBOX, check=True)
        subprocess.run(["git", *GIT_ID, "commit", "-q", "-m", "tracker edit"],
                       cwd=SANDBOX, check=True)
        try:
            legwork_runner.audit_session_window({"file": tracker}, claim_head)
            self.assertEqual(calls, [], "tracker-only edit must stay silent")
        finally:
            subprocess.run(["git", "reset", "-q", "--hard", claim_head],
                           cwd=SANDBOX, check=True)

    def _stub_review(self, verdict):
        """Stub the claude call and the reviewer so run_local_review exercises
        the runner's git write-back without touching the model or the network.
        Returns a restore() to undo the stubs."""
        orig_find = legwork_runner.find_claude
        orig_review = legwork_review.review
        legwork_runner.find_claude = lambda: "claude"
        legwork_review.review = lambda payload, model, claude: verdict
        return lambda: (setattr(legwork_runner, "find_claude", orig_find),
                        setattr(legwork_review, "review", orig_review))

    def test_local_review_applies_verdict_and_pushes(self):
        repo = make_git_repo("c-review-repo")
        path = write_project("c-review.md", status="review", repo=str(repo))
        commit_and_push("add local-review project")
        restore = self._stub_review(
            {"verdict": "pass", "summary": "looks done", "confidence": 0.95})
        try:
            legwork_runner.run_local_review(
                {"file": path, "repo_path": repo}, pre_head=None, detail=None)
        finally:
            restore()
        published = origin_file("projects/c-review.md")
        self.assertIn("status: queued", published, "pass requeues on the remote")
        self.assertIn("Reviewer passed: looks done", published)

    def test_local_review_escalate_flips_and_writes_brief(self):
        repo = make_git_repo("c-review-esc-repo")
        path = write_project("c-review-esc.md", status="review", repo=str(repo))
        commit_and_push("add escalate project")
        restore = self._stub_review(
            {"verdict": "escalate", "summary": "needs a human",
             "decision_brief": {"attempted": "a", "uncertain": "u",
                                "options": ["A. x", "B. y"],
                                "recommendation": "A"}})
        try:
            legwork_runner.run_local_review(
                {"file": path, "repo_path": repo}, pre_head=None, detail=None)
        finally:
            restore()
        published = origin_file("projects/c-review-esc.md")
        self.assertIn("status: escalated", published)
        self.assertIn("DECISION NEEDED", published)
        # An escalated project is never eligible to fire.
        ok, reason, _ = legwork_runner.assess(path)
        self.assertFalse(ok)
        self.assertIn("status is escalated", reason)

    def test_local_review_parks_when_call_fails(self):
        repo = make_git_repo("c-review-fail-repo")
        path = write_project("c-review-fail.md", status="queued", repo=str(repo))
        commit_and_push("add review-fail project")
        restore = self._stub_review(None)  # reviewer returned no verdict
        try:
            legwork_runner.run_local_review(
                {"file": path, "repo_path": repo}, pre_head=None, detail=None)
        finally:
            restore()
        published = origin_file("projects/c-review-fail.md")
        self.assertIn("status: review", published,
                      "a failed reviewer call parks the project for a human")
        self.assertIn("parked for human pickup", published)

    def test_local_review_skips_terminal_status(self):
        repo = make_git_repo("c-review-done-repo")
        path = write_project("c-review-done.md", status="done", repo=str(repo))
        commit_and_push("add done project")
        restore = self._stub_review({"verdict": "pass", "summary": "x"})
        try:
            legwork_runner.run_local_review(
                {"file": path, "repo_path": repo}, pre_head=None, detail=None)
        finally:
            restore()
        published = origin_file("projects/c-review-done.md")
        self.assertIn("status: done", published, "done must not be resurrected")
        self.assertNotIn("Reviewer passed", published)

    def test_local_review_revise_installs_fix_on_remote(self):
        repo = make_git_repo("c-review-rev-repo")
        path = write_project("c-review-rev.md", status="review", repo=str(repo))
        commit_and_push("add revise project")
        restore = self._stub_review(
            {"verdict": "revise", "summary": "off by one",
             "fix_prompt": "Read PROJECT.md.\n\nTask: fix the off-by-one.\n\n"
                           "Done when: tests pass.\n\nFinal step: run /wrap."})
        try:
            legwork_runner.run_local_review(
                {"file": path, "repo_path": repo}, pre_head=None, detail=None)
        finally:
            restore()
        published = origin_file("projects/c-review-rev.md")
        self.assertIn("status: queued", published)
        self.assertIn("fix the off-by-one", published)
        self.assertIn("Reviewer revise: off by one", published)

    def test_local_review_parks_unactionable_revise(self):
        repo = make_git_repo("c-review-noop-repo")
        path = write_project("c-review-noop.md", status="queued", repo=str(repo))
        commit_and_push("add unactionable revise project")
        # A revise with no fix prompt must NOT blow away the wrapped prompt;
        # the project is parked at review for a human instead.
        restore = self._stub_review(
            {"verdict": "revise", "summary": "vague", "fix_prompt": ""})
        try:
            legwork_runner.run_local_review(
                {"file": path, "repo_path": repo}, pre_head=None, detail=None)
        finally:
            restore()
        published = origin_file("projects/c-review-noop.md")
        self.assertIn("status: review", published)
        self.assertIn("Task: do one thing.", published,
                      "the wrapped prompt must survive an unactionable revise")
        self.assertIn("parked for human pickup", published)

    def test_park_for_review_skips_non_inflight(self):
        repo = make_git_repo("c-park-skip-repo")
        path = write_project("c-park-skip.md", status="escalated", repo=str(repo))
        commit_and_push("add already-escalated project")
        legwork_runner.park_for_review({"file": path})
        published = origin_file("projects/c-park-skip.md")
        self.assertIn("status: escalated", published,
                      "park must not touch a project that is not in flight")
        self.assertNotIn("parked for human pickup", published)

    def test_local_review_payload_scopes_to_session(self):
        repo = make_git_repo("c-payload-repo")
        subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                        "-m", "base"], cwd=repo, check=True)
        pre = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                             capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                        "-m", "session work"], cwd=repo, check=True)
        path = write_project("c-payload.md", status="review", repo=str(repo))
        commit_and_push("add payload project")
        payload = legwork_runner.local_review_payload(
            {"file": path, "repo_path": repo}, pre, detail=None)
        self.assertEqual(payload["repo"], "c-payload")
        self.assertEqual(payload["end_reason"], "normal")
        self.assertIn("session work", payload["session_commits"])
        self.assertNotIn("base", payload["session_commits"],
                         "evidence is scoped to the session, not the repo")
        self.assertIn("c-payload", payload["tracker_entry"])
        # A repair detail flips end_reason so the rubric treats it as recovery.
        recovered = legwork_runner.local_review_payload(
            {"file": path, "repo_path": repo}, pre, detail="exited")
        self.assertEqual(recovered["end_reason"], "runner-recovery")


class TestFireArgv(unittest.TestCase):
    """fire()'s argv construction, exercised end to end through a fake
    `claude` shim on PATH: the --settings guard, the allowedTools ladder,
    the directive-driven --model/--effort flags and the session timeout.
    A one-line regression here silently changes what a fired session is
    permitted to do, so the exact argv is pinned."""

    @classmethod
    def setUpClass(cls):
        cls.bin_dir = TMP / "fake-bin"
        cls.bin_dir.mkdir(exist_ok=True)
        helper = cls.bin_dir / "claude_shim.py"
        helper.write_text(
            "import json, os, sys, time\n"
            "with open(os.environ['FAKE_CLAUDE_ARGV'], 'w') as fh:\n"
            "    json.dump(sys.argv[1:], fh)\n"
            "sleep = float(os.environ.get('FAKE_CLAUDE_SLEEP', '0'))\n"
            "if sleep:\n"
            "    time.sleep(sleep)\n"
            "print(json.dumps({'type': 'result', 'is_error': False,\n"
            "                  'num_turns': 3, 'total_cost_usd': 0.05,\n"
            "                  'result': 'done'}))\n",
            encoding="utf-8")
        shim = cls.bin_dir / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{helper}" "$@"\n',
                        encoding="utf-8")
        shim.chmod(0o755)

    def setUp(self):
        self.argv_file = self.bin_dir / f"argv-{self.id().split('.')[-1]}.json"
        os.environ["FAKE_CLAUDE_ARGV"] = str(self.argv_file)
        self._path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin_dir}:{self._path}"
        self._send_alert = legwork_runner.send_alert
        legwork_runner.send_alert = lambda text: True

    def tearDown(self):
        legwork_runner.send_alert = self._send_alert
        os.environ["PATH"] = self._path
        del os.environ["FAKE_CLAUDE_ARGV"]
        legwork_runner.GUARD_SETTINGS.unlink(missing_ok=True)

    def fire_and_read_argv(self, fname, prompt=None):
        path = write_project(fname, repo=str(make_git_repo(fname[:-3] + "-repo")),
                             prompt=prompt)
        commit_and_push(f"add {fname}")
        ok, reason, details = legwork_runner.assess(path)
        self.assertTrue(ok, reason)
        outcome = legwork_runner.fire(details)
        self.assertIsNotNone(outcome, "the shim session must complete")
        import json as _json
        return _json.loads(self.argv_file.read_text(encoding="utf-8"))

    def after(self, argv, flag):
        self.assertIn(flag, argv)
        return argv[argv.index(flag) + 1]

    def test_argv_pins_the_permission_ladder(self):
        legwork_runner.write_guard_settings()
        argv = self.fire_and_read_argv(
            "f-argv.md",
            prompt=("Read PROJECT.md.\n\nTask: one thing.\nModel: haiku\n"
                    "Effort: low\n\nDone when: done.\n\nFinal step: run "
                    "/wrap to update the tracker and mint the next prompt."))
        self.assertEqual(self.after(argv, "--permission-mode"), "acceptEdits")
        self.assertEqual(self.after(argv, "--add-dir"),
                         str(legwork_runner.LEGWORK_DIR))
        self.assertEqual(self.after(argv, "--settings"),
                         str(legwork_runner.GUARD_SETTINGS))
        allowed = argv[argv.index("--allowedTools") + 1:argv.index("--model")]
        self.assertEqual(allowed, [
            "Bash(git:*)", "Bash(mkdir:*)",
            "Bash(python3 core/build_dashboard.py:*)",
            f"Bash(python3 {legwork_runner.LEGWORK_DIR}/core/"
            f"build_dashboard.py:*)",
        ])
        self.assertEqual(self.after(argv, "--model"), "haiku")
        self.assertEqual(self.after(argv, "--effort"), "low")
        prompt_arg = self.after(argv, "-p")
        self.assertIn("Autonomous legwork session", prompt_arg)
        self.assertIn("Task: one thing.", prompt_arg)
        self.assertNotIn("Model: haiku", prompt_arg,
                         "directives are extracted, not leaked to the session")

    def test_argv_omits_optional_flags_when_unconfigured(self):
        # No guard file and no directives: the ladder must not emit an
        # empty --settings, --model or --effort.
        legwork_runner.GUARD_SETTINGS.unlink(missing_ok=True)
        argv = self.fire_and_read_argv("f-plain.md")
        self.assertNotIn("--settings", argv)
        self.assertNotIn("--model", argv)
        self.assertNotIn("--effort", argv)
        self.assertEqual(self.after(argv, "--permission-mode"), "acceptEdits")
        self.assertIn("--allowedTools", argv)

    def test_session_past_the_timeout_is_terminated(self):
        path = write_project("f-slow.md",
                             repo=str(make_git_repo("f-slow-repo")))
        commit_and_push("add slow project")
        ok, reason, details = legwork_runner.assess(path)
        self.assertTrue(ok, reason)
        os.environ["FAKE_CLAUDE_SLEEP"] = "30"
        orig = (legwork_runner.SESSION_TIMEOUT, legwork_runner.GRACE)
        legwork_runner.SESSION_TIMEOUT, legwork_runner.GRACE = 1, 5
        started = time.time()
        try:
            legwork_runner.fire(details)
        finally:
            legwork_runner.SESSION_TIMEOUT, legwork_runner.GRACE = orig
            del os.environ["FAKE_CLAUDE_SLEEP"]
        self.assertLess(time.time() - started, 20,
                        "the timeout must terminate the session, not wait it out")
        log_text = legwork_runner.RUNNER_LOG.read_text(encoding="utf-8")
        self.assertIn("timeout: f-slow.md terminated", log_text)
        self.assertIn("status: review", path.read_text(encoding="utf-8"),
                      "a timed-out session parks for review")


class TestLockLifecycle(unittest.TestCase):
    """acquire_lock/release_lock and main()'s wiring around them: a dead or
    garbage lock is reclaimed, a live within-budget holder blocks the tick,
    and a wedged live holder is reclaimed past LOCK_MAX_AGE with one alert.
    A bug on either side stalls the queue or double-fires it."""

    def setUp(self):
        self.lock = legwork_runner.LOCK_FILE
        self.lock.unlink(missing_ok=True)
        self.alerts = []
        self._send_alert = legwork_runner.send_alert
        legwork_runner.send_alert = \
            lambda text: self.alerts.append(text) or True

    def tearDown(self):
        legwork_runner.send_alert = self._send_alert
        self.lock.unlink(missing_ok=True)

    def test_acquire_then_release_roundtrip(self):
        self.assertTrue(legwork_runner.acquire_lock())
        pid = self.lock.read_text(encoding="utf-8").split()[0]
        self.assertEqual(pid, str(os.getpid()))
        self.assertFalse(legwork_runner.acquire_lock(),
                         "a live within-budget holder must block")
        legwork_runner.release_lock()
        self.assertFalse(self.lock.exists())

    def test_dead_pid_lock_is_reclaimed(self):
        proc = subprocess.Popen(["sleep", "0"])
        proc.wait()  # a real PID that is now certainly dead
        self.lock.write_text(f"{proc.pid} {time.time()}", encoding="utf-8")
        self.assertTrue(legwork_runner.acquire_lock())
        self.assertEqual(self.lock.read_text(encoding="utf-8").split()[0],
                         str(os.getpid()),
                         "the reclaimed lock belongs to this run")

    def test_garbage_lock_is_reclaimed(self):
        for garbage in ("not a pid", ""):
            self.lock.write_text(garbage, encoding="utf-8")
            self.assertTrue(legwork_runner.acquire_lock(), repr(garbage))
            self.lock.unlink()

    def test_wedged_live_lock_is_reclaimed_past_max_age(self):
        held_since = time.time() - (legwork_runner.LOCK_MAX_AGE + 60)
        self.lock.write_text(f"{os.getpid()} {held_since}", encoding="utf-8")
        self.assertTrue(legwork_runner.acquire_lock())
        self.assertEqual(len(self.alerts), 1, "reclaiming a wedged lock alerts")
        self.assertIn("reclaim", self.alerts[0])

    def test_live_lock_within_budget_blocks_quietly(self):
        self.lock.write_text(f"{os.getpid()} {time.time()}", encoding="utf-8")
        self.assertFalse(legwork_runner.acquire_lock())
        self.assertEqual(self.alerts, [], "a normal long session is not alerted")

    def test_main_respects_the_lock_and_releases_it(self):
        ticks = []
        orig_tick = legwork_runner.tick
        orig_argv = sys.argv
        legwork_runner.tick = lambda dry_run=False: ticks.append(1)
        sys.argv = ["legwork_runner.py"]
        try:
            self.lock.write_text(f"{os.getpid()} {time.time()}",
                                 encoding="utf-8")
            legwork_runner.main()
            self.assertEqual(ticks, [], "a held lock must skip the tick")
            self.lock.unlink()
            legwork_runner.main()
            self.assertEqual(ticks, [1], "a free lock ticks exactly once")
            self.assertFalse(self.lock.exists(),
                             "main releases its own lock afterwards")
        finally:
            legwork_runner.tick = orig_tick
            sys.argv = orig_argv


class TestDashboard(unittest.TestCase):
    def test_parse_project_with_blocked_on(self):
        path = write_project(
            "t-dash.md", blocked_on="ICO registered",
            log_lines=["- 2026-06-02: second.", "- 2026-06-01: first."])
        parsed = build_dashboard.parse_project(path)
        self.assertEqual(parsed["blocked_on"], "ICO registered")
        self.assertEqual(parsed["log"][0], "2026-06-02: second.")
        pills = build_dashboard.pills(parsed)
        self.assertIn("blocked", pills)
        card = build_dashboard.card(parsed)
        # The blocked-on reason renders on the card (the label is bolded, so
        # the label and reason are not one contiguous run of text).
        self.assertIn("Blocked on:", card)
        self.assertIn("ICO registered", card)

    def test_unknown_status_treated_as_queued(self):
        path = write_project("t-badstatus.md", status="paused")
        parsed = build_dashboard.parse_project(path)
        self.assertEqual(parsed["status"], "queued")

    def test_changelog_groups_by_day(self):
        p1 = build_dashboard.parse_project(write_project(
            "t-cl1.md", log_lines=["- 2026-06-02: alpha moved."]))
        p2 = build_dashboard.parse_project(write_project(
            "t-cl2.md", log_lines=["- 2026-06-02: beta moved."]))
        html_out = build_dashboard.changelog_html([p1, p2])
        # Both entries share one day, so the date heads a single timeline group.
        self.assertEqual(html_out.count("2026-06-02"), 1)
        self.assertIn("alpha moved.", html_out)
        self.assertIn("beta moved.", html_out)

    def test_build_smoke(self):
        projects = [build_dashboard.parse_project(write_project("t-b1.md"))]
        page = build_dashboard.build(projects)
        self.assertIn("t-b1", page)
        self.assertIn("Copy prompt", page)

    def test_non_utf8_project_is_skipped_not_fatal(self):
        bad = SANDBOX / "projects" / "t-bad-bytes.md"
        bad.write_bytes(b"\xff\xfe\x00 not utf-8 \x9c")
        try:
            import contextlib
            import io
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(build_dashboard.parse_project(bad))
        finally:
            bad.unlink()

    def test_future_dated_project_reads_as_today(self):
        future = (date.today() + timedelta(days=3)).isoformat()
        parsed = build_dashboard.parse_project(write_project(
            "t-future.md", updated=future,
            log_lines=[f"- {future}: scheduled ahead."]))
        self.assertEqual(parsed["days_quiet"], 0,
                         "future dates clamp to 0, not negative freshness")

    def test_freshness_reads_all_log_bullets(self):
        # A hand-edited log is not always newest-first: the newest date
        # anywhere in the log decides freshness.
        old = (date.today() - timedelta(days=30)).isoformat()
        parsed = build_dashboard.parse_project(write_project(
            "t-logorder.md", updated=old,
            log_lines=[f"- {old}: created.",
                       f"- {date.today().isoformat()}: appended below."]))
        self.assertEqual(parsed["days_quiet"], 0)


class TestRunnerGuards(unittest.TestCase):
    """Small pure guards added in the correctness pass: numeric config
    parsing that must not raise at import, and ownership-checked lock
    release."""

    def test_env_number_garbage_falls_back(self):
        os.environ["LEGWORK_TEST_NUM"] = "abc"
        try:
            legwork_runner.CONFIG_WARNINGS.clear()
            self.assertEqual(
                legwork_runner._env_number("LEGWORK_TEST_NUM", 8, int), 8)
            self.assertEqual(len(legwork_runner.CONFIG_WARNINGS), 1)
            os.environ["LEGWORK_TEST_NUM"] = "inf"
            self.assertEqual(
                legwork_runner._env_number("LEGWORK_TEST_NUM", 0.0, float),
                0.0)
            os.environ["LEGWORK_TEST_NUM"] = "12.5"
            self.assertEqual(
                legwork_runner._env_number("LEGWORK_TEST_NUM", 0.0, float),
                12.5)
            os.environ["LEGWORK_TEST_NUM"] = ""
            self.assertEqual(
                legwork_runner._env_number("LEGWORK_TEST_NUM", 8, int), 8)
        finally:
            del os.environ["LEGWORK_TEST_NUM"]
            legwork_runner.CONFIG_WARNINGS.clear()

    def test_release_lock_respects_ownership(self):
        lock = legwork_runner.LOCK_FILE
        lock.write_text(f"{os.getpid() + 99} {time.time()}", encoding="utf-8")
        try:
            legwork_runner.release_lock()
            self.assertTrue(lock.exists(),
                            "another run's reclaimed lock must survive")
        finally:
            lock.unlink(missing_ok=True)
        lock.write_text(f"{os.getpid()} {time.time()}", encoding="utf-8")
        legwork_runner.release_lock()
        self.assertFalse(lock.exists(), "our own lock is released")


class TestHooks(unittest.TestCase):
    START = str(REPO / "core" / "session_start_hook.sh")
    END = str(REPO / "core" / "session_end_hook.sh")

    @classmethod
    def setUpClass(cls):
        cls.work = make_git_repo("t-hook-repo")
        subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                        "-m", "init"], cwd=cls.work, check=True)

    def run_hook(self, script, payload, url=None):
        env = dict(os.environ)
        env["LEGWORK_DIR"] = str(SANDBOX)
        env.pop("LEGWORK_WEBHOOK_URL", None)
        if url is not None:
            env["LEGWORK_WEBHOOK_URL"] = url
        return subprocess.run(
            ["bash", script], input=payload, text=True, env=env,
            capture_output=True, timeout=60)

    def hook_log_tail(self):
        log = SANDBOX / "hook.log"
        return log.read_text(encoding="utf-8").splitlines()[-1] if log.exists() else ""

    def test_start_writes_marker_and_prunes_stale(self):
        heads = SANDBOX / ".session-heads"
        heads.mkdir(exist_ok=True)
        stale = heads / "stale-marker"
        stale.write_text("x", encoding="utf-8")
        old = time.time() - 4 * 86400
        os.utime(stale, (old, old))
        payload = f'{{"session_id":"hk-1","cwd":"{self.work}"}}'
        self.run_hook(self.START, payload)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.work,
                              capture_output=True, text=True).stdout.strip()
        marker = (heads / "hk-1").read_text(encoding="utf-8").strip()
        # Marker is "<sha> <repo path>": sha first so a pre-upgrade reader
        # still gets a usable HEAD, repo path so SessionEnd can pin the repo.
        self.assertEqual(marker.split(" ", 1)[0], head)
        self.assertEqual(os.path.realpath(marker.split(" ", 1)[1]),
                         os.path.realpath(str(self.work)))
        self.assertFalse(stale.exists(), "stale marker should be pruned")

    def test_end_without_webhook_rebuilds_the_dashboard(self):
        # The lite path: no webhook means no reviewer to notify, so the
        # hook rebuilds the dashboard instead. Exercised with the real
        # builder copied into the sandbox's core/, so the rebuild runs
        # against sandbox projects and never the contributor's repo.
        import shutil
        core = SANDBOX / "core"
        core.mkdir(exist_ok=True)
        try:
            for mod in ("build_dashboard.py", "legwork_common.py"):
                shutil.copyfile(REPO / "core" / mod, core / mod)
            out = SANDBOX / "dashboard" / "index.html"
            self.run_hook(self.START,
                          f'{{"session_id":"hk-lite","cwd":"{self.work}"}}')
            self.run_hook(
                self.END,
                f'{{"session_id":"hk-lite","cwd":"{self.work}","reason":"exit"}}',
                url=None)
            tail = self.hook_log_tail()
            self.assertIn("rebuilt dashboard", tail)
            # The runner matches "sent:" to decide a review was delivered;
            # the lite path must never emit it.
            self.assertNotIn("sent:", tail)
            self.assertTrue(out.exists(), "dashboard/index.html is rebuilt")
            self.assertFalse(
                (SANDBOX / ".session-heads" / "hk-lite").exists(),
                "the lite path still consumes the session marker")
        finally:
            shutil.rmtree(core, ignore_errors=True)
            shutil.rmtree(SANDBOX / "dashboard", ignore_errors=True)

    def test_end_without_webhook_and_no_builder_fails_quietly(self):
        # No webhook and no core/build_dashboard.py under LEGWORK_DIR
        # (this sandbox): the rebuild cannot run, the hook logs the skip
        # and still exits 0 -- a broken hook must never block work.
        payload = f'{{"session_id":"hk-2","cwd":"{self.work}","reason":"exit"}}'
        result = self.run_hook(self.END, payload, url=None)
        self.assertEqual(result.returncode, 0)
        self.assertIn("dashboard rebuild failed", self.hook_log_tail())

    def test_end_skips_on_clear_even_without_webhook(self):
        # A clear/resume ending is a restart, not a finish: no rebuild.
        payload = f'{{"session_id":"hk-3b","cwd":"{self.work}","reason":"clear"}}'
        self.run_hook(self.END, payload, url=None)
        self.assertIn("skipped: reason=clear", self.hook_log_tail())

    def test_end_logs_sent_on_2xx(self):
        # The success path: a 2xx response logs "sent:", so the runner reads
        # the review as delivered. (A failed POST -> "post-failed:", which
        # keeps the runner's fallback alive; tested elsewhere.)
        import http.server
        import socketserver

        class _OK(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), _OK)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            self.run_hook(self.START,
                          f'{{"session_id":"hk-ok","cwd":"{self.work}"}}')
            subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                            "-m", "work"], cwd=self.work, check=True)
            self.run_hook(
                self.END,
                f'{{"session_id":"hk-ok","cwd":"{self.work}","reason":"exit"}}',
                url=f"http://127.0.0.1:{port}/x")
            tail = self.hook_log_tail()
            self.assertIn("sent:", tail)
            self.assertIn("http=200", tail)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_end_skips_on_clear(self):
        payload = f'{{"session_id":"hk-3","cwd":"{self.work}","reason":"clear"}}'
        self.run_hook(self.END, payload, url="http://127.0.0.1:9/x")
        self.assertIn("skipped: reason=clear", self.hook_log_tail())

    def test_end_skips_when_session_changed_nothing(self):
        payload = f'{{"session_id":"hk-4","cwd":"{self.work}"}}'
        self.run_hook(self.START, payload)
        self.run_hook(self.END,
                      f'{{"session_id":"hk-4","cwd":"{self.work}","reason":"exit"}}',
                      url="http://127.0.0.1:9/x")
        self.assertIn("no changes this session", self.hook_log_tail())

    def test_end_resolves_project_stem_from_repo_frontmatter(self):
        # A project's tracker file name can differ from its repo folder name:
        # the hook must report the project stem, not the repo folder name.
        repo = make_git_repo("t-alias-repo")
        subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                        "-m", "init"], cwd=repo, check=True)
        write_project("t-renamed.md", status="running", repo=str(repo))
        self.run_hook(self.START, f'{{"session_id":"hk-6","cwd":"{repo}"}}')
        subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                        "-m", "work"], cwd=repo, check=True)
        self.run_hook(
            self.END,
            f'{{"session_id":"hk-6","cwd":"{repo}","reason":"exit"}}',
            url="http://127.0.0.1:9/x")
        tail = self.hook_log_tail()
        # Dead port -> http=000 -> the failed POST logs "post-failed:", not
        # "sent:"; the resolved stem is what this test pins (assert the two
        # tokens separately, not the incidental spacing between them).
        self.assertIn("t-renamed", tail)
        self.assertIn("post-failed:", tail)
        self.assertNotIn("t-alias-repo", tail)

    def test_end_sends_and_consumes_marker(self):
        payload = f'{{"session_id":"hk-5","cwd":"{self.work}"}}'
        self.run_hook(self.START, payload)
        subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                        "-m", "work"], cwd=self.work, check=True)
        self.run_hook(self.END,
                      f'{{"session_id":"hk-5","cwd":"{self.work}","reason":"exit"}}',
                      url="http://127.0.0.1:9/x")
        tail = self.hook_log_tail()
        # A failed POST (dead port -> http=000) logs "post-failed:", so the
        # runner's hook_fired_since() stays False and its fallback fires; the
        # marker is still consumed before the POST is attempted.
        self.assertIn("post-failed:", tail)
        self.assertIn("http=000", tail)
        self.assertFalse((SANDBOX / ".session-heads" / "hk-5").exists(),
                         "consumed marker should be removed")

    def test_end_uses_marker_repo_when_cwd_drifts(self):
        # The real failure: a session opens in its target repo but ends with
        # its cwd in the legwork repo (it cd'd there to /wrap). The hook
        # must pin the target repo from the marker, not trust the end cwd,
        # or it misfiles the review and diffs the start sha against the wrong
        # repo (logging a false "no changes this session").
        repo = make_git_repo("t-drift-repo")
        subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                        "-m", "init"], cwd=repo, check=True)
        write_project("t-drift.md", status="running", repo=str(repo))
        self.run_hook(self.START, f'{{"session_id":"hk-7","cwd":"{repo}"}}')
        subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                        "-m", "build the thing"], cwd=repo, check=True)
        # End with the cwd pointing at a different repo than the session
        # opened in, the way /wrap leaves it in the legwork repo.
        drift = make_git_repo("t-drift-elsewhere")
        subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                        "-m", "init"], cwd=drift, check=True)
        self.run_hook(
            self.END,
            f'{{"session_id":"hk-7","cwd":"{drift}","reason":"exit"}}',
            url="http://127.0.0.1:9/x")
        tail = self.hook_log_tail()
        self.assertIn("t-drift", tail)
        self.assertIn("post-failed:", tail)
        self.assertNotIn("no changes this session", tail)
        self.assertNotIn("t-drift-elsewhere", tail)


class TestInstaller(unittest.TestCase):
    """The installer's pure builders: file contents and validation only, no
    launchctl/crontab/settings.json side effects (those are confirm-gated and
    run only under a real tty)."""

    def test_cron_schedule(self):
        self.assertEqual(legwork_install.cron_schedule(5), "*/5 * * * *")
        self.assertEqual(legwork_install.cron_schedule(15), "*/15 * * * *")
        self.assertEqual(legwork_install.cron_schedule(60), "0 */1 * * *")
        self.assertEqual(legwork_install.cron_schedule(120), "0 */2 * * *")
        self.assertEqual(legwork_install.cron_schedule(1440), "0 0 * * *")
        # Never emits the invalid */60 on the minute field.
        self.assertNotIn("*/60", legwork_install.cron_schedule(60))

    def test_render_plist_substitutes_and_retargets_interval(self):
        template = legwork_install.PLIST_TEMPLATE.read_text(encoding="utf-8")
        out = legwork_install.render_plist(
            template, "/Users/me/legwork", "/usr/bin/python3", 600)
        self.assertNotIn("__LEGWORK_DIR__", out)
        self.assertNotIn("__PYTHON__", out)
        self.assertIn("/Users/me/legwork/suite/legwork_runner.py", out)
        self.assertIn("/usr/bin/python3", out)
        self.assertIn("<integer>600</integer>", out)
        self.assertNotIn("<integer>300</integer>", out)

    def test_render_crontab_line_has_marker_and_schedule(self):
        line = legwork_install.render_crontab_line(
            "/Users/me/legwork", "/usr/bin/python3", "*/5 * * * *")
        self.assertTrue(line.startswith("*/5 * * * *"))
        self.assertIn("legwork_runner.py", line)
        self.assertIn(legwork_install.CRON_MARKER, line)
        self.assertIn(".runner-logs/cron.log", line)

    def test_render_config_local_mode(self):
        cfg = legwork_install.render_config({
            "legwork_dir": "$HOME/legwork", "daily_cap": 8,
            "daily_cost_cap": 0, "review_mode": "local",
            "reviewer_model": "claude-sonnet-4-6"})
        self.assertIn("LEGWORK_DIR=$HOME/legwork", cfg)
        self.assertIn("LEGWORK_DAILY_CAP=8", cfg)
        self.assertIn("LEGWORK_LOCAL_REVIEW=1", cfg)
        # No webhook in local mode; cost cap stays commented when zero.
        self.assertNotIn("LEGWORK_WEBHOOK_URL=", cfg)
        active = [ln for ln in cfg.splitlines()
                  if ln and not ln.startswith("#")]
        self.assertFalse(any(ln.startswith("LEGWORK_DAILY_COST_CAP=")
                             for ln in active))

    def test_render_config_n8n_mode(self):
        cfg = legwork_install.render_config({
            "legwork_dir": "$HOME/legwork", "daily_cap": 8,
            "daily_cost_cap": 10, "review_mode": "n8n",
            "webhook_url": "https://n8n.example/webhook/legwork-review",
            "alert_url": "https://n8n.example/webhook/legwork-alert",
            "reviewer_model": "claude-opus-4-8"})
        self.assertIn("LEGWORK_WEBHOOK_URL=https://n8n.example/webhook/"
                      "legwork-review", cfg)
        self.assertIn("LEGWORK_ALERT_URL=https://n8n.example/webhook/"
                      "legwork-alert", cfg)
        self.assertIn("LEGWORK_DAILY_COST_CAP=10", cfg)
        self.assertNotIn("LEGWORK_LOCAL_REVIEW=1", cfg)
        # A non-default reviewer model is emitted active, not commented.
        self.assertIn("REVIEWER_MODEL=claude-opus-4-8", cfg)

    def test_render_config_off_mode(self):
        cfg = legwork_install.render_config({
            "legwork_dir": "$HOME/legwork", "daily_cap": 8,
            "daily_cost_cap": 0, "review_mode": "off",
            "claude_config_dir": "$HOME/.claude-legwork"})
        active = [ln for ln in cfg.splitlines()
                  if ln and not ln.startswith("#")]
        # Off mode activates no reviewer line (the strings appear only in the
        # commented guidance).
        self.assertFalse(any(ln.startswith("LEGWORK_LOCAL_REVIEW")
                             for ln in active))
        self.assertFalse(any(ln.startswith("LEGWORK_WEBHOOK_URL")
                             for ln in active))
        self.assertIn("CLAUDE_CONFIG_DIR=$HOME/.claude-legwork", active)

    def test_render_config_round_trips_through_parse(self):
        # Whatever the installer writes, a re-run must be able to read back.
        cfg = legwork_install.render_config({
            "legwork_dir": "/srv/legwork", "daily_cap": 12,
            "daily_cost_cap": 25, "review_mode": "local",
            "reviewer_model": "claude-sonnet-4-6"})
        parsed = legwork_install.parse_config_text(cfg)
        self.assertEqual(parsed["LEGWORK_DIR"], "/srv/legwork")
        self.assertEqual(parsed["LEGWORK_DAILY_CAP"], "12")
        self.assertEqual(parsed["LEGWORK_DAILY_COST_CAP"], "25")
        self.assertEqual(parsed["LEGWORK_LOCAL_REVIEW"], "1")

    def test_parse_config_text_ignores_comments_and_strips_quotes(self):
        parsed = legwork_install.parse_config_text(
            '# a comment\n\nLEGWORK_DIR="/q/legwork"\nbad line no equals\n'
            "LEGWORK_DAILY_CAP=8\n")
        self.assertEqual(parsed["LEGWORK_DIR"], "/q/legwork")
        self.assertEqual(parsed["LEGWORK_DAILY_CAP"], "8")
        self.assertNotIn("bad line no equals", parsed)

    def test_merge_hooks_adds_both_events_from_empty(self):
        merged = legwork_install.merge_hooks({}, "/Users/me/legwork")
        start = merged["hooks"]["SessionStart"]
        end = merged["hooks"]["SessionEnd"]
        self.assertEqual(len(start), 1)
        self.assertEqual(len(end), 1)
        self.assertEqual(
            start[0]["hooks"][0]["command"],
            "/Users/me/legwork/core/session_start_hook.sh")
        self.assertEqual(
            end[0]["hooks"][0]["command"],
            "/Users/me/legwork/core/session_end_hook.sh")

    def test_merge_hooks_is_idempotent_and_preserves_others(self):
        base = {"model": "opus", "hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "/other/x.sh"}]}]}}
        once = legwork_install.merge_hooks(base, "/Users/me/legwork")
        twice = legwork_install.merge_hooks(once, "/Users/me/legwork")
        # The unrelated hook survives; ours is added exactly once.
        self.assertEqual(len(twice["hooks"]["SessionStart"]), 2)
        self.assertEqual(len(twice["hooks"]["SessionEnd"]), 1)
        self.assertEqual(twice["model"], "opus")

    def test_plan_verb_installs_covers_commands_and_skill(self):
        dest = Path("/tmp/fake-home/.claude")
        pairs = legwork_install.plan_verb_installs(REPO / "core", dest)
        names = {src.name for src, _ in pairs}
        for verb in ("add.md", "wrap.md", "pickup.md", "vision.md",
                     "log.md", "shelve.md"):
            self.assertIn(verb, names)
        self.assertIn("SKILL.md", names)
        # Destinations mirror the source layout under the dest base, and
        # every source is a real file in this repo.
        by_dest = {d: s for s, d in pairs}
        self.assertIn(dest / "commands" / "wrap.md", by_dest)
        self.assertIn(dest / "skills" / "legwork-tracker" / "SKILL.md",
                      by_dest)
        for src, d in pairs:
            self.assertTrue(src.is_file(), src)
            self.assertTrue(str(d).startswith(str(dest)), d)

    def test_validators(self):
        self.assertEqual(legwork_install.validate_int("8")[1], 8)
        self.assertFalse(legwork_install.validate_int("nope")[0])
        self.assertFalse(legwork_install.validate_int("0", low=1)[0])
        self.assertEqual(legwork_install.validate_cost("12.5")[1], 12.5)
        self.assertFalse(legwork_install.validate_cost("-1")[0])
        # inf renders a config the runner cannot read back; nan compares
        # false against everything, silently disabling the cap.
        self.assertFalse(legwork_install.validate_cost("inf")[0])
        self.assertFalse(legwork_install.validate_cost("nan")[0])
        self.assertTrue(
            legwork_install.validate_url("https://h/x")[0])
        self.assertFalse(legwork_install.validate_url("ftp://h/x")[0])
        self.assertFalse(legwork_install.validate_minutes("0")[0])
        self.assertTrue(legwork_install.validate_minutes("5")[0])
        self.assertTrue(legwork_install.validate_dir("/abs/path")[0])
        self.assertTrue(legwork_install.validate_dir("~/legwork")[0])
        self.assertFalse(legwork_install.validate_dir("relative/path")[0])

    def test_ask_aborts_instead_of_looping_when_answer_cannot_change(self):
        # Under --yes (or Ctrl-D at a prompt) the answer is always the
        # default; a default that fails validation used to spin the while
        # loop forever. It must abort non-zero instead.
        import contextlib
        import io
        ui = legwork_install.UI(color=False, unicode=False)
        wiz = legwork_install.Wizard(ui, assume_yes=True)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                wiz.ask("Daily fire cap", default="abc",
                        validate=legwork_install.validate_int)
            self.assertEqual(ctx.exception.code, 2)
            # A valid default still sails through.
            self.assertEqual(
                wiz.ask("Daily fire cap", default="8",
                        validate=legwork_install.validate_int), 8)

    def test_review_mode_default_keeps_the_configured_mode(self):
        self.assertEqual(legwork_install.review_mode_default({}), 0)
        self.assertEqual(legwork_install.review_mode_default(
            {"LEGWORK_WEBHOOK_URL": "https://n8n.example/webhook/x"}), 1)
        self.assertEqual(legwork_install.review_mode_default(
            {"LEGWORK_LOCAL_REVIEW": "1"}), 0)
        self.assertEqual(legwork_install.review_mode_default(
            {"LEGWORK_DIR": "/x"}), 2)

    def test_render_plist_escapes_xml(self):
        import plistlib
        template = legwork_install.PLIST_TEMPLATE.read_text(encoding="utf-8")
        out = legwork_install.render_plist(
            template, "/Users/me/A & B/legwork", "/usr/bin/python3", 300)
        data = plistlib.loads(out.encode("utf-8"))  # must stay valid XML
        self.assertIn("A & B", str(data))

    def test_render_crontab_line_quotes_and_escapes(self):
        line = legwork_install.render_crontab_line(
            "/Users/me/My Projects/legwork", "/usr/bin/python3",
            "*/5 * * * *")
        self.assertIn("'/Users/me/My Projects/legwork/suite/"
                      "legwork_runner.py'", line)
        line = legwork_install.render_crontab_line(
            "/Users/me/pct%dir/legwork", "/usr/bin/python3", "*/5 * * * *")
        self.assertIn("pct\\%dir", line)
        self.assertNotIn("pct%dir", line.replace("pct\\%dir", ""))

    def test_merge_hooks_repoints_after_repo_move(self):
        base = legwork_install.merge_hooks({}, "/old/legwork")
        moved = legwork_install.merge_hooks(base, "/new/legwork")
        for event, script in (("SessionStart", "session_start_hook.sh"),
                              ("SessionEnd", "session_end_hook.sh")):
            entries = moved["hooks"][event]
            self.assertEqual(len(entries), 1, "re-pointed, not duplicated")
            self.assertEqual(entries[0]["hooks"][0]["command"],
                             f"/new/legwork/core/{script}")

    def test_merge_hooks_repoints_pre_split_scripts_entries(self):
        # A settings.json written before the core/ split points at
        # <dir>/scripts/session_*_hook.sh. The match is by script filename,
        # so a re-install must re-point those entries at core/, not
        # duplicate them.
        base = {"hooks": {
            "SessionStart": [{"hooks": [{
                "type": "command",
                "command": "/me/legwork/scripts/session_start_hook.sh"}]}],
            "SessionEnd": [{"hooks": [{
                "type": "command",
                "command": "/me/legwork/scripts/session_end_hook.sh"}]}],
        }}
        merged = legwork_install.merge_hooks(base, "/me/legwork")
        for event, script in (("SessionStart", "session_start_hook.sh"),
                              ("SessionEnd", "session_end_hook.sh")):
            entries = merged["hooks"][event]
            self.assertEqual(len(entries), 1, "re-pointed, not duplicated")
            self.assertEqual(entries[0]["hooks"][0]["command"],
                             f"/me/legwork/core/{script}")

    def test_render_config_persists_tick_minutes(self):
        cfg = legwork_install.render_config({
            "legwork_dir": "/srv/legwork", "daily_cap": 8,
            "daily_cost_cap": 0, "review_mode": "local",
            "reviewer_model": "claude-sonnet-4-6", "interval_minutes": 7})
        parsed = legwork_install.parse_config_text(cfg)
        self.assertEqual(parsed["LEGWORK_TICK_MINUTES"], "7")

    def test_wordmark_alignment_and_ascii_fallback(self):
        ascii_ui = legwork_install.UI(color=False, unicode=False)
        rows = legwork_install.wordmark("LEGWORK", ascii_ui)
        self.assertEqual(len(rows), 5)
        # Every row is the same width, so the columns never drift.
        self.assertEqual(len({len(r) for r in rows}), 1)
        # ASCII/no-color mode emits no ANSI escapes.
        head = legwork_install.masthead(ascii_ui)
        self.assertNotIn("\033", head)
        self.assertIn("+", head)

    @staticmethod
    def _args(**kw):
        import argparse
        ns = argparse.Namespace(yes=False, lite=False, with_commands=False,
                                with_launchd=False, with_hooks=False)
        ns.__dict__.update(kw)
        return ns

    def test_flag_truth_table_for_the_outside_the_repo_steps(self):
        # The --yes footgun fix, pinned: a non-interactive run never touches
        # anything outside the repo (force=False skips the step) unless the
        # matching --with-* flag opts in; interactive runs ask (None).
        args = self._args
        cases = [
            (args(yes=True), True, (False, False, False)),
            (args(yes=True, with_commands=True), True, (True, False, False)),
            (args(yes=True, with_launchd=True), True, (False, True, False)),
            (args(yes=True, with_hooks=True), True, (False, False, True)),
            (args(yes=True, with_commands=True, with_launchd=True,
                  with_hooks=True), True, (True, True, True)),
            (args(), True, (None, None, None)),  # interactive: each step asks
            (args(with_launchd=True), True, (None, True, None)),
            (args(), False, (False, False, False)),  # piped stdin == --yes
            (args(with_hooks=True), False, (False, False, True)),
        ]
        for ns, interactive, expected in cases:
            self.assertEqual(
                legwork_install.plan_forces(ns, interactive), expected,
                f"args={ns} interactive={interactive}")

    def test_plan_level_truth_table(self):
        # The level 1 / level 2 fork: --lite pins 1, --with-launchd (the
        # timer is autonomy) pins 2, --with-hooks carries no level signal
        # (the hooks earn their keep at either level), a re-run pre-fills
        # the recorded level (a pre-fork config reads as a full install),
        # a fresh install defaults to the manual loop.
        args = self._args
        cases = [
            (args(), {}, (1, False)),                       # fresh: lite
            (args(yes=True), {}, (1, False)),               # bare --yes too
            (args(lite=True), {}, (1, True)),
            (args(with_launchd=True), {}, (2, True)),
            (args(with_hooks=True), {}, (1, False)),        # level-neutral
            (args(yes=True, with_hooks=True), {}, (1, False)),
            (args(lite=True, with_hooks=True), {}, (1, True)),
            (args(with_hooks=True), {"LEGWORK_LEVEL": "2"}, (2, False)),
            (args(), {"LEGWORK_LEVEL": "1"}, (1, False)),   # re-run pre-fill
            (args(), {"LEGWORK_LEVEL": "2"}, (2, False)),
            (args(), {"LEGWORK_DIR": "/x"}, (2, False)),    # pre-fork config
            (args(lite=True), {"LEGWORK_LEVEL": "2"}, (1, True)),  # flag wins
        ]
        for ns, existing, expected in cases:
            self.assertEqual(
                legwork_install.plan_level(ns, existing), expected,
                f"args={ns} existing={existing}")
        with self.assertRaises(ValueError):
            legwork_install.plan_level(
                self._args(lite=True, with_launchd=True), {})

    def test_render_config_level_1_is_lite_and_round_trips(self):
        cfg = legwork_install.render_config(
            {"level": 1, "legwork_dir": "/srv/legwork"})
        parsed = legwork_install.parse_config_text(cfg)
        self.assertEqual(parsed, {"LEGWORK_LEVEL": "1",
                                  "LEGWORK_DIR": "/srv/legwork"})
        # The round trip that makes re-runs pre-fill the level.
        self.assertEqual(
            legwork_install.plan_level(self._args(), parsed), (1, False))

    def test_render_config_level_2_records_the_level(self):
        for values in (
            {"level": 2, "legwork_dir": "/srv/legwork", "daily_cap": 8,
             "daily_cost_cap": 0, "review_mode": "local",
             "reviewer_model": "claude-sonnet-4-6", "interval_minutes": 5},
            # A values dict without the key (older callers) is a full install.
            {"legwork_dir": "/srv/legwork", "daily_cap": 8,
             "daily_cost_cap": 0, "review_mode": "off",
             "reviewer_model": "", "interval_minutes": 5},
        ):
            parsed = legwork_install.parse_config_text(
                legwork_install.render_config(values))
            self.assertEqual(parsed.get("LEGWORK_LEVEL"), "2", values)
            self.assertIn("LEGWORK_DAILY_CAP", parsed)

    def test_write_repo_files_level_1_skips_runner_logs(self):
        orig_repo = legwork_install.REPO
        try:
            for level, expect_logs in ((1, False), (2, True)):
                repo = Path(tempfile.mkdtemp(dir=str(TMP)))
                legwork_install.REPO = repo
                values = {"level": level, "legwork_dir": str(repo),
                          "daily_cap": 8, "daily_cost_cap": 0,
                          "review_mode": "off", "reviewer_model": "",
                          "interval_minutes": 5}
                legwork_install.write_repo_files(values)
                parsed = legwork_install.parse_config_text(
                    (repo / "config").read_text(encoding="utf-8"))
                self.assertEqual(parsed.get("LEGWORK_LEVEL"), str(level))
                self.assertTrue((repo / "projects").is_dir())
                self.assertEqual((repo / ".runner-logs").is_dir(),
                                 expect_logs, f"level={level}")
        finally:
            legwork_install.REPO = orig_repo

    def _run_main_with_stubbed_steps(self, argv, repo=None):
        """main(argv) with every side-effect step stubbed out. Returns
        (rc, received): which steps ran with which force, plus the values
        write_repo_files saw under the 'write_repo_files' key."""
        import contextlib
        import io
        received = {}
        names = ("install_verbs", "install_timer", "install_hooks")
        orig = {n: getattr(legwork_install, n)
                for n in (*names, "write_repo_files", "run_doctor", "REPO")}

        def step(name):
            def stub(wiz, values, force=None):
                received[name] = force
                return []
            return stub

        for name in names:
            setattr(legwork_install, name, step(name))

        def fake_write(values):
            received["write_repo_files"] = values
            return ["config (stubbed)"]

        legwork_install.write_repo_files = fake_write
        legwork_install.run_doctor = lambda wiz: None
        legwork_install.REPO = repo or Path(tempfile.mkdtemp(dir=str(TMP)))
        stdin = sys.stdin
        sys.stdin = io.StringIO("")  # not a tty: non-interactive
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = legwork_install.main(argv)
        finally:
            sys.stdin = stdin
            for name, value in orig.items():
                setattr(legwork_install, name, value)
        return rc, received

    def test_main_lite_reaches_hooks_but_never_the_timer(self):
        # --yes --lite: a level-1 config is written, the verbs and hook
        # steps still run (both skipped headless without their --with-*
        # flag), and the timer step is never called at all -- not merely
        # forced to skip.
        rc, received = self._run_main_with_stubbed_steps(
            ["--yes", "--lite", "--no-color"])
        self.assertEqual(rc, 0)
        self.assertEqual(received["write_repo_files"]["level"], 1)
        self.assertEqual(received["install_verbs"], False)
        self.assertEqual(received["install_hooks"], False)
        self.assertNotIn("install_timer", received)

    def test_main_bare_yes_on_a_fresh_clone_defaults_to_level_1(self):
        rc, received = self._run_main_with_stubbed_steps(
            ["--yes", "--no-color"])
        self.assertEqual(rc, 0)
        self.assertEqual(received["write_repo_files"]["level"], 1)
        self.assertNotIn("install_timer", received)

    def test_main_yes_rerun_keeps_an_existing_level_2_install(self):
        # A pre-fork config (no LEGWORK_LEVEL) came from the full flow: a
        # --yes re-run over it must not silently downgrade to level 1.
        repo = Path(tempfile.mkdtemp(dir=str(TMP)))
        (repo / "config").write_text("LEGWORK_DIR=/srv/legwork\n",
                                     encoding="utf-8")
        rc, received = self._run_main_with_stubbed_steps(
            ["--yes", "--no-color"], repo=repo)
        self.assertEqual(rc, 0)
        self.assertEqual(received["write_repo_files"]["level"], 2)
        self.assertEqual(received["install_timer"], False)
        self.assertEqual(received["install_hooks"], False)

    def test_main_lite_with_hooks_installs_the_lite_hooks(self):
        # --lite --with-hooks used to be a contradiction; with the
        # webhook-less hooks it is the headless lite-with-hooks install:
        # level 1, the hook step forced on, the timer never reached.
        rc, received = self._run_main_with_stubbed_steps(
            ["--yes", "--lite", "--with-hooks", "--no-color"])
        self.assertEqual(rc, 0)
        self.assertEqual(received["write_repo_files"]["level"], 1)
        self.assertEqual(received["install_hooks"], True)
        self.assertNotIn("install_timer", received)

    def test_main_lite_with_launchd_is_refused(self):
        repo = Path(tempfile.mkdtemp(dir=str(TMP)))
        rc, received = self._run_main_with_stubbed_steps(
            ["--yes", "--lite", "--with-launchd", "--no-color"], repo=repo)
        self.assertEqual(rc, 2)
        self.assertEqual(received, {}, "nothing ran, nothing was written")

    def test_main_yes_wires_the_skip_forces_into_the_steps(self):
        # main(["--yes", "--with-hooks"]) over a recorded level-2 config,
        # end to end with the side-effect steps stubbed: the two un-flagged
        # steps receive force=False, the flagged one True, and the run
        # exits 0.
        repo = Path(tempfile.mkdtemp(dir=str(TMP)))
        (repo / "config").write_text(
            "LEGWORK_LEVEL=2\nLEGWORK_DIR=/srv/legwork\n", encoding="utf-8")
        rc, received = self._run_main_with_stubbed_steps(
            ["--yes", "--with-hooks", "--no-color"], repo=repo)
        self.assertEqual(rc, 0)
        self.assertEqual(received["write_repo_files"]["level"], 2)
        self.assertEqual(
            {name: received[name] for name in
             ("install_verbs", "install_timer", "install_hooks")},
            {"install_verbs": False, "install_timer": False,
             "install_hooks": True})

    def test_main_yes_with_hooks_on_a_fresh_clone_is_lite_with_hooks(self):
        # --with-hooks no longer drags a fresh install to level 2: a bare
        # --yes --with-hooks is the one-line lite-with-hooks install.
        rc, received = self._run_main_with_stubbed_steps(
            ["--yes", "--with-hooks", "--no-color"])
        self.assertEqual(rc, 0)
        self.assertEqual(received["write_repo_files"]["level"], 1)
        self.assertEqual(received["install_hooks"], True)
        self.assertNotIn("install_timer", received)

    def test_main_without_tty_or_yes_refuses(self):
        import contextlib
        import io
        stdin = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = legwork_install.main(["--no-color"])
        finally:
            sys.stdin = stdin
        self.assertEqual(rc, 2)


def tearDownModule():
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
