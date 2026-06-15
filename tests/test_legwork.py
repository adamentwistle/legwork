"""Tests for the legwork runner, dashboard builder and hooks.

Stdlib only, like everything in scripts/. Run from the repo root:

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
os.environ["LEGWORK_DIR"] = str(SANDBOX)
sys.path.insert(0, str(REPO / "scripts"))

import build_dashboard  # noqa: E402
import legwork_runner  # noqa: E402

(SANDBOX / "projects").mkdir(parents=True)

GIT_ID = ["-c", "user.email=test@test", "-c", "user.name=test"]


def make_git_repo(name):
    path = TMP / name
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
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
        self.assertEqual(clean, "Task: y.")

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
            legwork_runner.RUNNER_LOG.unlink()

    def test_fires_today_ignores_other_days(self):
        with open(legwork_runner.RUNNER_LOG, "w", encoding="utf-8") as fh:
            fh.write("2020-01-01 09:00:00  fired t-old.md in x\n")
        try:
            self.assertEqual(legwork_runner.fires_today("t-old.md"), 0)
        finally:
            legwork_runner.RUNNER_LOG.unlink()


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
            legwork_runner.STATE_FILE.unlink()

    def test_porcelain_path(self):
        self.assertEqual(
            legwork_runner.porcelain_path(" M projects/alpha.md"),
            "projects/alpha.md")
        self.assertEqual(
            legwork_runner.porcelain_path("A  scripts/legwork_runner.py"),
            "scripts/legwork_runner.py")
        self.assertEqual(
            legwork_runner.porcelain_path("R  old.md -> projects/new.md"),
            "projects/new.md")


class TestConcurrentRunner(unittest.TestCase):
    """Claim and wrap race paths: parallel claims serialise behind
    WRITE_LOCK, remote movement (n8n write-back, parallel wraps) is rebased
    over, and one tick fires every eligible project except repo-sharers.
    The sandbox legwork dir becomes a real git clone with a bare origin
    standing in for the GitHub remote."""

    @classmethod
    def setUpClass(cls):
        cls.origin = TMP / "legwork-origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                        str(cls.origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=SANDBOX,
                       check=True)
        subprocess.run(["git", "config", "user.email", "test@test"],
                       cwd=SANDBOX, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=SANDBOX,
                       check=True)
        subprocess.run(["git", "remote", "add", "origin", str(cls.origin)],
                       cwd=SANDBOX, check=True)
        # Mirror the real repo's ignores: runtime files must stay untracked
        # or their churn would read as uncommitted tracked changes and
        # block ticks mid-suite.
        (SANDBOX / ".gitignore").write_text(
            "runner.log\n.runner-state.json\nhook.log\n.session-heads/\n",
            encoding="utf-8")
        cls.commit_and_push("init sandbox")

    def setUp(self):
        # Belt and braces: nothing in these tests may hit the network.
        self._send_alert = legwork_runner.send_alert
        legwork_runner.send_alert = lambda text: True

    def tearDown(self):
        legwork_runner.send_alert = self._send_alert

    @classmethod
    def commit_and_push(cls, message):
        subprocess.run(["git", "add", "-A"], cwd=SANDBOX, check=True)
        subprocess.run(["git", *GIT_ID, "commit", "-q", "--allow-empty",
                        "-m", message], cwd=SANDBOX, check=True)
        subprocess.run(["git", "push", "-q", "-u", "origin", "main"],
                       cwd=SANDBOX, check=True)

    def remote_commit(self, relpath, content, message):
        """Move the origin from a second clone, the way the n8n Contents
        API or a parallel session's wrap moves the real remote."""
        clone = Path(tempfile.mkdtemp(prefix="clone-", dir=str(TMP)))
        subprocess.run(["git", "clone", "-q", str(self.origin), str(clone)],
                       check=True)
        target = clone / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=clone, check=True)
        subprocess.run(["git", *GIT_ID, "commit", "-q", "-m", message],
                       cwd=clone, check=True)
        subprocess.run(["git", "push", "-q"], cwd=clone, check=True)

    def origin_file(self, relpath):
        return subprocess.run(
            ["git", "show", f"main:{relpath}"], cwd=self.origin,
            capture_output=True, text=True).stdout

    def origin_subjects(self):
        return subprocess.run(
            ["git", "log", "--format=%s", "main"], cwd=self.origin,
            capture_output=True, text=True).stdout

    def test_parallel_claims_both_publish(self):
        write_project("c-claim-a.md", repo=str(make_git_repo("c-claim-a-repo")))
        write_project("c-claim-b.md", repo=str(make_git_repo("c-claim-b-repo")))
        self.commit_and_push("add claim race projects")
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
                          self.origin_file(f"projects/{fname}"))

    def test_claim_publishes_over_remote_movement(self):
        path = write_project("c-race.md",
                             repo=str(make_git_repo("c-race-repo")))
        self.commit_and_push("add remote race project")
        self.remote_commit("reviewer-note.txt", "verdict",
                           "n8n: verdict appended")
        sha = legwork_runner.claim({"file": path})
        self.assertTrue(sha, "claim should land despite the moved remote")
        subjects = self.origin_subjects()
        self.assertIn("legwork: runner fires c-race", subjects)
        self.assertIn("n8n: verdict appended", subjects)

    def test_claim_dropped_when_remote_flips_status(self):
        path = write_project("c-flip.md",
                             repo=str(make_git_repo("c-flip-repo")))
        self.commit_and_push("add flip race project")
        flipped = path.read_text(encoding="utf-8").replace(
            "status: queued", "status: escalated")
        self.remote_commit("projects/c-flip.md", flipped,
                           "n8n: escalate c-flip")
        sha = legwork_runner.claim({"file": path})
        self.assertIsNone(sha, "a status moved off queued drops the claim")
        self.assertIn("status: escalated",
                      path.read_text(encoding="utf-8"))

    def test_claim_consumes_fire_once(self):
        path = write_project("c-fonce.md", autonomy="", vision=False,
                             repo=str(make_git_repo("c-fonce-repo")),
                             fire_once="2026-06-11")
        self.commit_and_push("add fire_once project")
        sha = legwork_runner.claim({"file": path})
        self.assertTrue(sha, "fire_once claim should publish")
        published = self.origin_file("projects/c-fonce.md")
        self.assertIn("status: running", published)
        self.assertNotIn("fire_once", published,
                         "the claim must consume the one-shot consent")

    def test_remote_pause_arriving_with_the_pull_stops_the_tick(self):
        write_project("c-paused.md",
                      repo=str(make_git_repo("c-paused-repo")))
        self.commit_and_push("add project behind remote pause")
        self.remote_commit(".runner-pause-remote", "Paused via Telegram\n",
                           "n8n: pause the runner")
        legwork_runner.save_state(
            {"last_heartbeat": date.today().isoformat()})
        fired = []
        original = legwork_runner.fire
        legwork_runner.fire = lambda p: fired.append(p["file"].name)
        try:
            legwork_runner.tick()
        finally:
            legwork_runner.fire = original
            legwork_runner.STATE_FILE.unlink()
            # Lift the pause for the tests that follow.
            (SANDBOX / ".runner-pause-remote").unlink()
            self.commit_and_push("lift remote pause")
        self.assertEqual(fired, [], "a freshly pulled pause must stop firing")

    def test_push_with_rebase_recovers(self):
        marker = SANDBOX / "c-local-note.txt"
        marker.write_text("local work", encoding="utf-8")
        subprocess.run(["git", "add", str(marker)], cwd=SANDBOX, check=True)
        subprocess.run(["git", *GIT_ID, "commit", "-q", "-m",
                        "local: unpushed work"], cwd=SANDBOX, check=True)
        self.remote_commit("c-remote-note.txt", "remote work",
                           "remote: moved first")
        self.assertTrue(legwork_runner.push_with_rebase(SANDBOX))
        subjects = self.origin_subjects()
        self.assertIn("local: unpushed work", subjects)
        self.assertIn("remote: moved first", subjects)

    def test_repair_flips_unwrapped_session_and_pushes(self):
        path = write_project("c-repair.md", status="running",
                             repo=str(make_git_repo("c-repair-repo")))
        self.commit_and_push("add unwrapped session project")
        detail = legwork_runner.repair_unwrapped(
            {"file": path}, exit_code=1, minutes=7)
        self.assertIn("exit 1", detail)
        text = path.read_text(encoding="utf-8")
        self.assertIn("status: review", text)
        self.assertIn("Runner: autonomous session exited without wrapping",
                      text)
        self.assertIn("status: review",
                      self.origin_file("projects/c-repair.md"))

    def test_repair_transient_requeues_instead_of_review(self):
        path = write_project("c-transient.md", status="running",
                             repo=str(make_git_repo("c-transient-repo")))
        self.commit_and_push("add transient crash project")
        detail = legwork_runner.repair_unwrapped(
            {"file": path}, exit_code=1, minutes=1, transient=True)
        self.assertIn("re-queued for retry", detail)
        text = path.read_text(encoding="utf-8")
        self.assertIn("status: queued", text)
        self.assertIn("transient API error", text)
        self.assertIn("status: queued",
                      self.origin_file("projects/c-transient.md"))

    def test_repair_skips_session_that_wrapped(self):
        path = write_project("c-wrapped.md", status="review",
                             repo=str(make_git_repo("c-wrapped-repo")))
        self.commit_and_push("add wrapped session project")
        self.assertIsNone(legwork_runner.repair_unwrapped(
            {"file": path}, exit_code=0, minutes=3))

    def test_repair_recognizes_wrapped_session_left_running(self):
        # The session wrapped (committed a tracker edit with its own honest
        # message) but forgot to flip status off running. The runner must
        # move it to review without claiming it exited without wrapping.
        path = write_project("c-wrap-running.md", status="running",
                             repo=str(make_git_repo("c-wrap-running-repo")))
        self.commit_and_push("add wrapped-but-running project")
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
                      self.origin_file("projects/c-wrap-running.md"))

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
        self.commit_and_push("add autocommit project")
        p = SANDBOX / "projects" / "c-autocommit.md"
        p.write_text(p.read_text(encoding="utf-8") +
                     "\n- 2026-06-14: manual wrap, never committed.\n",
                     encoding="utf-8")
        legwork_runner.save_state({"last_heartbeat": date.today().isoformat()})
        fired = []
        original = legwork_runner.fire
        legwork_runner.fire = lambda pr: fired.append(pr["file"].name)
        try:
            legwork_runner.tick()
        finally:
            legwork_runner.fire = original
            legwork_runner.STATE_FILE.unlink()
        clean = subprocess.run(["git", "status", "--porcelain"], cwd=SANDBOX,
                               capture_output=True, text=True).stdout.strip()
        self.assertEqual(clean, "", "tracker edit should have been committed")
        self.assertIn("runner auto-commits tracker edits",
                      self.origin_subjects())
        self.assertIn("c-autocommit.md", fired,
                      "project should fire the same tick it was committed")

    def test_tick_blocks_on_dirty_file_outside_projects(self):
        # A dirty tracked file outside projects/ must still stall the tick;
        # only tracker-only edits are auto-committed.
        (SANDBOX / "scripts").mkdir(exist_ok=True)
        f = SANDBOX / "scripts" / "c-dirty.txt"
        f.write_text("v1\n", encoding="utf-8")
        self.commit_and_push("add tracked non-tracker file")
        f.write_text("v2 uncommitted\n", encoding="utf-8")
        legwork_runner.save_state({"last_heartbeat": date.today().isoformat()})
        fired = []
        original = legwork_runner.fire
        legwork_runner.fire = lambda pr: fired.append(pr["file"].name)
        try:
            legwork_runner.tick()
        finally:
            legwork_runner.fire = original
            # Revert so later tests are not blocked by this dirty file.
            f.write_text("v1\n", encoding="utf-8")
            self.commit_and_push("revert dirty non-tracker file")
            legwork_runner.STATE_FILE.unlink()
        self.assertEqual(fired, [],
                         "a dirty file outside projects/ must stall firing")

    def test_tick_fires_every_eligible_and_defers_shared_repo(self):
        shared = make_git_repo("c-shared-repo")
        write_project("c-tick-a.md", repo=str(shared), updated="2026-06-01")
        write_project("c-tick-b.md", repo=str(shared), updated="2026-06-02")
        write_project("c-tick-c.md", repo=str(make_git_repo("c-solo-repo")),
                      updated="2026-06-03")
        self.commit_and_push("add tick fan-out projects")
        legwork_runner.save_state(
            {"last_heartbeat": date.today().isoformat()})
        fired = []
        original = legwork_runner.fire
        legwork_runner.fire = lambda p: fired.append(p["file"].name)
        try:
            legwork_runner.tick()
        finally:
            legwork_runner.fire = original
            legwork_runner.STATE_FILE.unlink()
        self.assertIn("c-tick-a.md", fired)
        self.assertIn("c-tick-c.md", fired)
        self.assertNotIn("c-tick-b.md", fired,
                         "repo-sharing project defers to a later tick")


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
        self.assertIn("Blocked on: ICO registered", card)

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
        self.assertEqual(html_out.count("Tue 02 Jun 2026"), 1)
        self.assertIn("alpha moved.", html_out)
        self.assertIn("beta moved.", html_out)

    def test_build_smoke(self):
        projects = [build_dashboard.parse_project(write_project("t-b1.md"))]
        page = build_dashboard.build(projects)
        self.assertIn("t-b1", page)
        self.assertIn("Copy prompt", page)


class TestHooks(unittest.TestCase):
    START = str(REPO / "scripts" / "session_start_hook.sh")
    END = str(REPO / "scripts" / "session_end_hook.sh")

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

    def test_end_skips_without_webhook_url(self):
        payload = f'{{"session_id":"hk-2","cwd":"{self.work}","reason":"exit"}}'
        self.run_hook(self.END, payload, url=None)
        self.assertIn("LEGWORK_WEBHOOK_URL not set", self.hook_log_tail())

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
        self.assertIn("t-renamed  sent:", tail)
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
        self.assertIn("sent:", tail)
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
        self.assertIn("t-drift  sent:", tail)
        self.assertNotIn("no changes this session", tail)
        self.assertNotIn("t-drift-elsewhere", tail)


def tearDownModule():
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
