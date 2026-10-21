"""Tests for orchestration/bin/switchboard-wait.

The switchboard entries in fixtures/switchboard/ are sanitised from real
runs: two decisions open at once (legwork-145 and legwork-146), a decision
answered under a later CYCLE (legwork-151 answered under legwork-152), and
a decision replaced by a later one (recipe-box-20 says "Replaces
recipe-box-17"). herdr is replaced by a stub on PATH whose answer each test
sets.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "orchestration" / "bin" / "switchboard-wait"
FIXTURES = HERE / "fixtures" / "switchboard"


def load_script():
    loader = importlib.machinery.SourceFileLoader("switchboard_wait", str(SCRIPT))
    spec = importlib.util.spec_from_loader("switchboard_wait", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def blocks(name):
    """Entry id -> its text, header to the next header, ANSWERED lines kept."""
    text = (FIXTURES / name).read_text()
    heads = list(re.finditer(r"^## (\S+) ", text, re.M))
    out = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out[m.group(1)] = text[m.start():end].rstrip("\n") + "\n\n"
    return out


def unanswered(text):
    return "".join(ln for ln in text.splitlines(True) if not ln.startswith("ANSWERED "))


BOARD = blocks("legwork-switchboard.md")
RECIPES = blocks("recipe-box-switchboard.md")

STUB = """#!{python}
import json, sys, time
with open({calls!r}, "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
cfg = json.load(open({cfg!r}))
time.sleep(cfg.get("sleep", 0))
# Real herdr prints errors as JSON on stderr and exits 1.
if cfg["status"] == "gone" or cfg["status"].startswith("error:"):
    code = "agent_not_found" if cfg["status"] == "gone" else cfg["status"][6:]
    print(json.dumps({{"error": {{"code": code}}, "id": "cli:agent:get"}}), file=sys.stderr)
    sys.exit(1)
else:
    print(json.dumps({{"id": "cli:agent:get", "result": {{"agent": {{
        "agent_status": cfg["status"], "state_change_seq": cfg.get("seq", 1),
        "pane_id": sys.argv[3]}}, "type": "agent_info"}}}}))
"""


class PortableTests(unittest.TestCase):
    """What runs on Windows too; SwitchboardCase needs a POSIX herdr stub."""

    def setUp(self):
        self.script = load_script()
        self.script.STATE_FILE = os.devnull

    def test_split_target_keeps_the_pane_colon_and_a_drive_letter(self):
        split = self.script.split_target
        self.assertEqual(split("/src/legwork:w1X:p2"), ("/src/legwork", "w1X:p2"))
        self.assertEqual(split("~/q:none"), ("~/q", "none"))
        self.assertEqual(split("C:/src/legwork:none"), ("C:/src/legwork", "none"))
        self.assertEqual(split(r"E:\src\q:w1:p2"), (r"E:\src\q", "w1:p2"))

    def test_a_drive_letter_alone_is_not_a_pane(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.script.main(["--status", "C:/src/legwork"]), 2)

    def test_status_and_save_run_without_herdr(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "legwork"
            (repo / ".legwork").mkdir(parents=True)
            board = repo / ".legwork" / "switchboard.md"
            board.write_text(unanswered(BOARD["legwork-145"]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.script.main(["--status", f"{repo}:none"]), 0)
            self.assertIn("legwork legwork-145 DECISION", out.getvalue())
            # the cursor save takes the file lock: flock, or msvcrt on Windows
            cursor = str(repo / ".legwork" / "switchboard-wait.cursor")
            self.script.save_states({cursor: {str(board): "legwork-145"}}, {cursor: {}})
            self.assertEqual(json.loads(Path(cursor).read_text()),
                             {str(board): "legwork-145"})


@unittest.skipIf(os.name == "nt", "the herdr stub is a POSIX shebang script on a :-joined PATH")
class SwitchboardCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.script = load_script()
        self.script.USAGE_CACHE = str(self.tmp / "no-usage.json")
        self.script.POLL = 0.2
        self.state_file = self.tmp / "state.md"
        self.script.STATE_FILE = str(self.state_file)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        self.cfg = self.tmp / "herdr.json"
        self.calls = self.tmp / "herdr-calls.txt"
        stub = bindir / "herdr"
        stub.write_text(STUB.format(python=sys.executable, cfg=str(self.cfg),
                                    calls=str(self.calls)))
        stub.chmod(0o755)
        self.set_path(f"{bindir}:/usr/bin:/bin")
        self.repo = self.tmp / "legwork"
        self.board = self.repo / ".legwork" / "switchboard.md"
        self.cursor = self.repo / ".legwork" / "switchboard-wait.cursor"
        self.herdr("working")

    def set_path(self, value):
        patcher = mock.patch.dict(os.environ, {"PATH": value})
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch_attr(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def herdr(self, status="working", seq=1, sleep=0):
        self.cfg.write_text(json.dumps({"status": status, "seq": seq, "sleep": sleep}))

    def herdr_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def write(self, *texts):
        self.board.parent.mkdir(parents=True, exist_ok=True)
        self.board.write_text("".join(texts))

    def append(self, text):
        with open(self.board, "a") as f:
            f.write(text)

    def main(self, argv):
        """Call main directly: (return code, stdout lines)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.script.main(argv)
        return rc, out.getvalue().splitlines()

    def run_wait(self, timeout=1):
        start = time.time()
        rc, lines = self.main(["--timeout", str(timeout), f"{self.repo}:w1:p2"])
        return rc, lines, time.time() - start


class SwitchboardWaitTests(SwitchboardCase):
    # (a) legwork-151: a DECISION written while no wait was running.
    def test_cold_start_reports_open_decision(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        rc, lines, took = self.run_wait(timeout=30)
        self.assertEqual((rc, lines), (0, ["legwork legwork-151 DECISION"]))
        self.assertLess(took, 5, "an open decision on start must return at once")

    # (b) legwork-152: the CYCLE is the latest entry, legwork-151 still open.
    def test_cold_start_reports_latest_cycle_152(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"],
                   unanswered(BOARD["legwork-152"]))
        rc, lines, _ = self.run_wait()
        self.assertEqual(rc, 0)
        self.assertEqual(lines, ["legwork legwork-151 DECISION",
                                 "legwork legwork-152 CYCLE"])

    # (c) legwork-165: the CYCLE is the latest entry, legwork-164 still open.
    def test_cold_start_reports_latest_cycle_165(self):
        self.write(BOARD["legwork-163"], BOARD["legwork-164"],
                   unanswered(BOARD["legwork-165"]))
        rc, lines, _ = self.run_wait()
        self.assertEqual(rc, 0)
        self.assertEqual(lines, ["legwork legwork-164 DECISION",
                                 "legwork legwork-165 CYCLE"])

    # (d) The fixture: "ANSWERED legwork-151: B" sits under the legwork-152 CYCLE.
    def test_answered_decision_is_not_reported(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"], BOARD["legwork-152"])
        rc, lines, _ = self.run_wait()
        self.assertEqual(rc, 0)
        self.assertEqual(lines, ["legwork legwork-152 CYCLE"])

    def test_cycle_that_is_not_latest_is_not_reported_on_start(self):
        self.write(BOARD["legwork-163"], BOARD["legwork-164"], BOARD["legwork-165"],
                   "## legwork-166 UPDATE 2026-09-26 07:00\nPublished v0.1.0.\n")
        rc, lines, _ = self.run_wait()
        self.assertEqual((rc, lines), (124, ["timeout"]))

    # (e) recipe-box-20 says "Replaces recipe-box-17 (no need to answer recipe-box-17)."
    def test_replaced_decision_is_not_reported(self):
        self.write(RECIPES["recipe-box-17"], RECIPES["recipe-box-18"],
                   RECIPES["recipe-box-19"], RECIPES["recipe-box-20"])
        rc, lines, _ = self.run_wait()
        self.assertEqual(rc, 0)
        self.assertEqual(lines, ["legwork recipe-box-20 DECISION"])

    # (f) A herdr call that hangs cannot hold the wait past its deadline.
    def test_hung_herdr_does_not_outlast_deadline(self):
        self.write(BOARD["legwork-150"])
        self.herdr("working", sleep=60)
        rc, lines, took = self.run_wait(timeout=2)
        self.assertEqual((rc, lines), (124, ["timeout"]))
        self.assertLess(took, 4)

    def test_herdr_timeout_is_reported_once_as_unreachable(self):
        self.patch_attr(self.script, "HERDR_TIMEOUT", 1)
        self.write(BOARD["legwork-150"])
        self.herdr("working", sleep=60)
        rc, lines, took = self.run_wait(timeout=20)
        self.assertEqual((rc, lines), (0, ["legwork pane unreachable"]))
        self.assertLess(took, 3)
        rc, lines, took = self.run_wait(timeout=3)
        self.assertEqual((rc, lines), (124, ["timeout"]))
        self.assertLess(took, 5)

    def test_herdr_call_is_made_with_a_30s_timeout(self):
        real_run = self.script.subprocess.run
        timeouts = []

        def run(*args, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            return real_run(*args, **kwargs)

        self.patch_attr(self.script.subprocess, "run", run)
        self.write(BOARD["legwork-150"])
        self.herdr("idle", seq=2)
        self.assertEqual(self.run_wait(timeout=600)[:2], (0, ["legwork pane idle"]))
        self.assertEqual(timeouts, [30])

    # (g) The cursor advances, a second start reports nothing new, and an
    # entry written between waits is reported on the next start.
    def test_cursor_advances_and_second_start_is_quiet(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        rc, lines, _ = self.run_wait()
        self.assertEqual((rc, lines), (0, ["legwork legwork-151 DECISION"]))
        self.assertEqual(json.loads(self.cursor.read_text())[str(self.board)], "legwork-151")

        rc, lines, _ = self.run_wait()
        self.assertEqual((rc, lines), (124, ["timeout"]))
        self.assertEqual(json.loads(self.cursor.read_text())[str(self.board)], "legwork-151")

        self.append(unanswered(BOARD["legwork-152"]))
        rc, lines, _ = self.run_wait()
        self.assertEqual((rc, lines), (0, ["legwork legwork-152 CYCLE"]))
        self.assertEqual(json.loads(self.cursor.read_text())[str(self.board)], "legwork-152")

    def test_cursor_does_not_move_when_nothing_is_printed(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"], BOARD["legwork-152"])
        self.cursor.write_text(json.dumps({str(self.board): "legwork-150"}))
        self.append("## legwork-153 UPDATE 2026-09-25 20:31\nResumed.\n")
        rc, lines, _ = self.run_wait()
        self.assertEqual((rc, lines), (124, ["timeout"]))
        self.assertEqual(json.loads(self.cursor.read_text())[str(self.board)], "legwork-150")

    def test_new_decision_during_wait_is_reported(self):
        self.write(BOARD["legwork-150"])
        t = threading.Timer(0.5, self.append, [BOARD["legwork-151"]])
        t.start()
        try:
            rc, lines, took = self.run_wait(timeout=10)
        finally:
            t.cancel()
        self.assertEqual((rc, lines), (0, ["legwork legwork-151 DECISION"]))
        self.assertLess(took, 3)

    def test_idle_pane_is_reported_once_per_state_change(self):
        self.write(BOARD["legwork-150"])
        self.herdr("idle", seq=5)
        self.assertEqual(self.run_wait()[:2], (0, ["legwork pane idle"]))
        self.assertEqual(self.run_wait()[:2], (124, ["timeout"]))
        self.herdr("idle", seq=9)
        self.assertEqual(self.run_wait()[:2], (0, ["legwork pane idle"]))

    def test_pane_going_idle_during_wait_is_reported(self):
        self.write(BOARD["legwork-150"])
        t = threading.Timer(0.5, self.herdr, ["done"], {"seq": 2})
        t.start()
        try:
            rc, lines, _ = self.run_wait(timeout=10)
        finally:
            t.cancel()
        self.assertEqual((rc, lines), (0, ["legwork pane done"]))

    def test_gone_pane_is_reported_on_start(self):
        self.write(BOARD["legwork-150"])
        self.herdr("gone")
        self.assertEqual(self.run_wait()[:2], (0, ["legwork pane gone"]))

    def test_usage_floor_is_reported(self):
        usage = self.tmp / "usage.json"
        usage.write_text(json.dumps({"weeklyUsage": 50, "fableUsage": 10}))
        self.patch_attr(self.script, "USAGE_CACHE", str(usage))
        self.write(BOARD["legwork-150"])
        t = threading.Timer(0.5, usage.write_text,
                            [json.dumps({"weeklyUsage": 50, "fableUsage": 99})])
        t.start()
        try:
            rc, lines, _ = self.run_wait(timeout=10)
        finally:
            t.cancel()
        self.assertEqual((rc, lines), (0, ["usage weekly Fable at 99%"]))

    def test_bad_arguments_exit_2(self):
        self.assertEqual(self.main([])[0], 2)
        self.assertEqual(self.main(["--timeout", "x", "repo:pane"])[0], 2)
        self.assertEqual(self.main(["no-pane"])[0], 2)

    # --status: full state for check-ins, never touches the cursor.
    def test_status_reports_decision_the_cursor_already_reported(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        self.assertEqual(self.run_wait()[:2], (0, ["legwork legwork-151 DECISION"]))
        self.assertEqual(self.run_wait()[:2], (124, ["timeout"]))
        rc, lines = self.main(["--status", f"{self.repo}:w1:p2"])
        self.assertEqual(rc, 0)
        first = BOARD["legwork-151"].splitlines()[1]
        self.assertTrue(first.startswith("Attempted: plugin cache option A is built as 327"))
        self.assertEqual(lines, [f"legwork legwork-151 DECISION {first}",
                                 "legwork pane working", "legwork wait none"])

    def test_status_leaves_cursor_file_byte_identical(self):
        self.write(BOARD["legwork-163"], BOARD["legwork-164"],
                   unanswered(BOARD["legwork-165"]))
        self.herdr("idle", seq=3)
        before = b'{"not": "rewritten",   "keep": "these bytes"}\n'
        self.cursor.write_bytes(before)
        start = time.time()
        rc, lines = self.main(["--timeout", "20", "--status", f"{self.repo}:w1:p2"])
        self.assertTrue(rc == 0 and time.time() - start < 3)
        self.assertEqual([ln.split(" ", 3)[:3] for ln in lines[:2]], [
            ["legwork", "legwork-164", "DECISION"], ["legwork", "legwork-165", "CYCLE"]])
        self.assertEqual(lines[2], "legwork pane idle")
        self.assertEqual(self.cursor.read_bytes(), before)

    def test_status_without_cursor_creates_none(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        self.assertEqual(self.main(["--status", f"{self.repo}:w1:p2"])[0], 0)
        self.assertFalse(self.cursor.exists())

    # Review of PR 4, finding 1: a multibyte character cut mid-write.
    def test_non_utf8_bytes_do_not_crash_the_wait(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        with open(self.board, "ab") as f:
            f.write(b"## legwork-152 UPDATE 2026-09-25 20:01\nHalf a euro sign \xe2\x82")
        rc, lines, _ = self.run_wait()
        self.assertEqual((rc, lines), (0, ["legwork legwork-151 DECISION"]))

    # Finding 2: a trailing CYCLE or DONE needs the switchboard while the pane is stopped,
    # so it is reported on every start, not once.
    def test_trailing_cycle_is_reported_every_start_while_pane_idle(self):
        self.write(BOARD["legwork-163"], BOARD["legwork-164"], BOARD["legwork-165"])
        self.herdr("idle", seq=4)
        self.assertEqual(self.run_wait()[:2],
                         (0, ["legwork legwork-165 CYCLE", "legwork pane idle"]))
        self.assertEqual(self.run_wait()[:2], (0, ["legwork legwork-165 CYCLE"]))
        self.herdr("working", seq=5)
        self.assertEqual(self.run_wait()[:2], (124, ["timeout"]))

    def test_trailing_done_is_reported_every_start_while_pane_done(self):
        self.write(BOARD["legwork-150"],
                   "## legwork-174 DONE 2026-09-26 09:57\nRoadmap 3 row 17.\n")
        self.herdr("done", seq=7)
        self.assertEqual(self.run_wait()[:2],
                         (0, ["legwork legwork-174 DONE", "legwork pane done"]))
        self.assertEqual(self.run_wait()[:2], (0, ["legwork legwork-174 DONE"]))

    def test_open_decision_stays_report_once_while_pane_idle(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        self.herdr("idle", seq=4)
        self.assertEqual(self.run_wait()[:2],
                         (0, ["legwork legwork-151 DECISION", "legwork pane idle"]))
        self.assertEqual(self.run_wait()[:2], (124, ["timeout"]))

    # Finding 3: only agent_not_found means the pane is gone.
    def test_herdr_server_not_running_is_not_gone(self):
        self.write(BOARD["legwork-150"])
        self.herdr("error:server_not_running")
        self.assertEqual(self.run_wait()[:2], (0, ["legwork pane unreachable"]))
        self.assertEqual(self.run_wait()[:2], (124, ["timeout"]))

    # Finding 4: a file rewritten shorter during a wait.
    def test_file_rewritten_shorter_during_wait(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-163"])
        t = threading.Timer(0.5, self.write, [BOARD["legwork-151"]])
        t.start()
        try:
            rc, lines, took = self.run_wait(timeout=6)
        finally:
            t.cancel()
        self.assertEqual((rc, lines), (0, ["legwork legwork-151 DECISION"]))
        self.assertLess(took, 3)

    # Finding 5: a repo with no .legwork directory yet.
    def test_missing_legwork_dir_still_keeps_pane_state(self):
        self.repo.mkdir()
        self.herdr("idle", seq=4)
        self.assertEqual(self.run_wait()[:2], (0, ["legwork pane idle"]))
        self.assertEqual(self.run_wait()[:2], (124, ["timeout"]))
        self.assertTrue(self.cursor.exists())

    def test_missing_repo_is_not_created(self):
        self.herdr("idle", seq=4)
        self.assertEqual(self.run_wait()[:2], (0, ["legwork pane idle"]))
        self.assertFalse(self.repo.exists())

    # Finding 6: two waits on the same repo share one cursor file.
    def test_concurrent_wait_keeps_the_other_waits_cursor(self):
        self.write(BOARD["legwork-150"])
        other = {"panes": {"p9": {"status": "idle", "seq": 3}},
                 str(self.board): "legwork-150"}
        t1 = threading.Timer(0.3, self.cursor.write_text, [json.dumps(other)])
        t2 = threading.Timer(0.6, self.append, [BOARD["legwork-151"]])
        t1.start()
        t2.start()
        try:
            rc, lines, _ = self.run_wait(timeout=6)
        finally:
            t1.cancel()
            t2.cancel()
        self.assertEqual((rc, lines), (0, ["legwork legwork-151 DECISION"]))
        saved = json.loads(self.cursor.read_text())
        self.assertEqual(saved["panes"]["p9"], {"status": "idle", "seq": 3})
        self.assertIn("w1:p2", saved["panes"])
        self.assertEqual(saved[str(self.board)], "legwork-151")

    def test_save_never_moves_a_cursor_backwards(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"],
                   unanswered(BOARD["legwork-152"]))
        self.cursor.write_text(json.dumps({str(self.board): "legwork-152"}))
        self.script.save_states({str(self.cursor): {str(self.board): "legwork-151"}},
                                {str(self.cursor): {}})
        self.assertEqual(json.loads(self.cursor.read_text())[str(self.board)], "legwork-152")

    # Finding 7: "Replaces" only counts at the start of a line.
    def test_replaces_in_prose_does_not_close_a_decision(self):
        self.write(RECIPES["recipe-box-17"],
                   "## recipe-box-18 UPDATE 2026-09-25 19:30\n"
                   "The new loader Replaces recipe-box-17 style parsing.\n\n")
        self.assertEqual(self.run_wait()[:2], (0, ["legwork recipe-box-17 DECISION"]))

    # Finding 8: a repeated header id must not move the cursor past a later entry.
    def test_repeated_header_id_uses_the_first_match(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"],
                   "## legwork-150 UPDATE 2026-09-25 20:00\nA repeated id.\n\n")
        self.cursor.write_text(json.dumps({str(self.board): "legwork-150"}))
        self.assertEqual(self.run_wait()[:2], (0, ["legwork legwork-151 DECISION"]))

    # Finding 9: ANSWERED with whitespace, not a colon, after the id.
    def test_answered_without_colon_is_recognised(self):
        for line in ["ANSWERED legwork-151 B\n", "ANSWERED legwork-151"]:
            with self.subTest(line=line):
                self.write(BOARD["legwork-150"], BOARD["legwork-151"], line)
                self.assertEqual(self.run_wait()[:2], (124, ["timeout"]))

    # Finding 10: herdr's own "unknown" state is a pane state, not a failed call.
    def test_herdr_unknown_state_is_kept_as_herdr_reports_it(self):
        self.write(BOARD["legwork-150"])
        self.herdr("unknown", seq=4)
        self.assertEqual(self.run_wait()[:2], (124, ["timeout"]))
        self.assertEqual(self.main(["--status", f"{self.repo}:w1:p2"]),
                         (0, ["legwork pane unknown", "legwork wait none"]))
        self.herdr("error:server_not_running")
        self.assertEqual(self.main(["--status", f"{self.repo}:w1:p2"]),
                         (0, ["legwork pane unreachable", "legwork wait none"]))

    # Finding 11: a usage cache with strings in it.
    def test_string_usage_values_are_coerced_or_skipped(self):
        usage = self.tmp / "usage.json"
        usage.write_text(json.dumps({"weeklyUsage": "50", "fableUsage": "n/a"}))
        self.patch_attr(self.script, "USAGE_CACHE", str(usage))
        self.write(BOARD["legwork-150"])
        t = threading.Timer(0.5, usage.write_text,
                            [json.dumps({"weeklyUsage": "99", "fableUsage": None})])
        t.start()
        try:
            rc, lines, _ = self.run_wait(timeout=10)
        finally:
            t.cancel()
        self.assertEqual((rc, lines), (0, ["usage weekly all-models at 99%"]))

    # Coverage the review asked for.
    def test_two_open_decisions_are_both_reported(self):
        self.write(BOARD["legwork-145"], unanswered(BOARD["legwork-146"]))
        rc, lines, _ = self.run_wait()
        self.assertEqual((rc, lines), (0, ["legwork legwork-145 DECISION",
                                           "legwork legwork-146 DECISION"]))
        self.assertEqual(json.loads(self.cursor.read_text())[str(self.board)], "legwork-146")
        self.assertEqual(self.run_wait()[:2], (124, ["timeout"]))

    def test_second_open_decision_after_cursor_on_first(self):
        self.write(BOARD["legwork-145"], unanswered(BOARD["legwork-146"]))
        self.cursor.write_text(json.dumps({str(self.board): "legwork-145"}))
        self.assertEqual(self.run_wait()[:2], (0, ["legwork legwork-146 DECISION"]))

    def test_cursor_is_written_by_temp_file_and_replace(self):
        real_replace = self.script.os.replace
        calls = []

        def replace(src, dst):
            if dst == str(self.cursor):  # the pids file is saved the same way
                calls.append((src, dst, json.loads(Path(src).read_text())))
                self.assertFalse(Path(dst).exists())
            return real_replace(src, dst)

        self.patch_attr(self.script.os, "replace", replace)
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        self.assertEqual(self.run_wait()[:2], (0, ["legwork legwork-151 DECISION"]))
        self.assertEqual(len(calls), 1)
        src, dst, content = calls[0]
        self.assertTrue(dst == str(self.cursor) and src != dst)
        self.assertEqual(content[str(self.board)], "legwork-151")
        self.assertFalse(list(self.cursor.parent.glob("*.tmp")))

    def test_missing_herdr_binary_is_unreachable_once(self):
        empty = self.tmp / "empty-bin"
        empty.mkdir()
        self.set_path(str(empty))
        self.write(BOARD["legwork-150"])
        self.assertEqual(self.run_wait()[:2], (0, ["legwork pane unreachable"]))
        self.assertEqual(self.run_wait()[:2], (124, ["timeout"]))

    def test_corrupt_cursor_re_reports_and_is_rewritten(self):
        for junk in [b"not json{", b"[1, 2]", b"\xff\xfe", b""]:
            with self.subTest(junk=junk):
                self.write(BOARD["legwork-150"], BOARD["legwork-151"])
                self.cursor.write_bytes(junk)
                self.assertEqual(self.run_wait()[:2], (0, ["legwork legwork-151 DECISION"]))
                self.assertEqual(json.loads(self.cursor.read_text())[str(self.board)],
                                 "legwork-151")

    # REPO:none means no Herdr: the file is watched and herdr is never called.
    def test_no_pane_watches_the_file_only(self):
        self.herdr("idle")
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        rc, lines = self.main(["--timeout", "1", f"{self.repo}:none"])
        self.assertEqual((rc, lines), (0, ["legwork legwork-151 DECISION"]))
        rc, lines = self.main(["--timeout", "1", f"{self.repo}:none"])
        self.assertEqual((rc, lines), (124, ["timeout"]))
        self.assertEqual(self.herdr_calls(), [])

    def test_status_with_no_pane_prints_no_pane_line(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        rc, lines = self.main(["--status", f"{self.repo}:none"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("legwork legwork-151 DECISION "))
        self.assertEqual(lines[1], "legwork wait none")
        self.assertEqual(self.herdr_calls(), [])

    # A wait that is running lists itself, and a SIGTERM takes it off again.
    def spawn_wait(self, preexec_fn=None):
        env = dict(os.environ, SWITCHBOARD_USAGE_CACHE=str(self.tmp / "no-usage.json"))
        return subprocess.Popen([sys.executable, str(SCRIPT), "--timeout", "20",
                                 f"{self.repo}:w1:p2"], stdout=subprocess.DEVNULL,
                                env=env, preexec_fn=preexec_fn)

    def last_status_line(self):
        return self.main(["--status", f"{self.repo}:w1:p2"])[1][-1]

    def test_status_shows_a_running_wait_and_drops_it_on_sigterm(self):
        self.write(BOARD["legwork-150"])
        proc = self.spawn_wait()
        try:
            self.assertTrue(until(lambda: self.last_status_line().startswith(
                "legwork wait running since ")))
            self.assertIn(f"(pid {proc.pid})", self.last_status_line())
        finally:
            proc.terminate()
            proc.wait(5)
        self.assertEqual(self.last_status_line(), "legwork wait none")
        self.assertNotIn(str(proc.pid), (self.repo / ".legwork" / PIDS).read_text())

    # nohup starts a wait with SIGHUP ignored; it must stay ignored.
    def test_inherited_sighup_ignore_is_kept(self):
        self.write(BOARD["legwork-150"])
        proc = self.spawn_wait(
            preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
        try:
            self.assertTrue(until(lambda: self.last_status_line().startswith(
                "legwork wait running")))
            proc.send_signal(signal.SIGHUP)
            time.sleep(0.5)
            self.assertIsNone(proc.poll())
        finally:
            proc.terminate()
            proc.wait(5)

    # A live process that only looks like a wait, or that started at another
    # time than the one listed, is not the listed wait.
    def test_status_ignores_a_reused_pid_with_a_wait_like_name(self):
        self.write(BOARD["legwork-150"])
        fake = subprocess.Popen(["bash", "-c", "exec -a switchboard-wait-fake sleep 30"])
        try:
            until(lambda: "switchboard-wait-fake" in subprocess.run(
                ["ps", "-o", "command=", "-p", str(fake.pid)],
                capture_output=True, text=True).stdout)
            (self.repo / ".legwork" / PIDS).write_text(json.dumps({str(fake.pid): 1.0}))
            self.assertEqual(self.last_status_line(), "legwork wait none")
        finally:
            fake.kill()
            fake.wait(5)


def until(cond, limit=5):
    end = time.time() + limit
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


# A GRANT written by the switchboard took the same number as an
# orchestrator entry: legwork-2 was a GRANT, a DECISION and two DONEs. With
# the cursor on the last DONE the next start matched the first legwork-2
# and reported the DONE again, three times in three minutes.
DUP = ("## legwork-1 DONE 2026-09-26 16:52\nRoadmap 1 wrapped.\n\n"
       "## legwork-2 GRANT 2026-09-26 17:07\nUser: \"b\"\nScope: item 8.\n\n"
       "## legwork-2 DECISION 2026-09-26 17:10\nAttempted: x\n\n"
       "ANSWERED legwork-2: b (2026-09-26 17:10)\n\n"
       "## legwork-2 DONE 2026-09-26 17:10\nRoadmap 2 wrapped.\n\n")
PIDS = "switchboard-wait.pids"


class FileOnlyTests(unittest.TestCase):
    """Waits on REPO:none never call herdr, so these run on Windows too."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.script = load_script()
        self.script.USAGE_CACHE = str(self.tmp / "no-usage.json")
        self.script.POLL = 0.2
        self.state_file = self.tmp / "state.md"
        self.script.STATE_FILE = str(self.state_file)
        self.repo = self.tmp / "legwork"
        self.board = self.repo / ".legwork" / "switchboard.md"
        self.cursor = self.repo / ".legwork" / "switchboard-wait.cursor"
        self.pids = self.repo / ".legwork" / PIDS
        self.board.parent.mkdir(parents=True)

    def write(self, *texts):
        self.board.write_text("".join(texts))

    def append(self, text):
        with open(self.board, "a") as f:
            f.write(text)

    def main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.script.main(argv)
        return rc, out.getvalue().splitlines()

    def run_wait(self):
        return self.main(["--timeout", "1", f"{self.repo}:none"])

    def status(self):
        return self.main(["--status", f"{self.repo}:none"])[1]

    def test_cursor_on_a_repeated_id_does_not_re_report_the_later_entry(self):
        self.write(DUP)
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-2 DONE"]))
        self.assertEqual(json.loads(self.cursor.read_text())[str(self.board)], "legwork-2#2")
        self.assertEqual(self.run_wait(), (124, ["timeout"]))

    def test_cursor_on_a_repeated_id_still_reports_what_follows(self):
        self.write(DUP)
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-2 DONE"]))
        self.append("## legwork-3 DECISION 2026-09-26 17:20\nAttempted: y\n\n")
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-3 DECISION"]))

    def test_merge_compares_repeated_ids_by_position(self):
        self.write(DUP)
        self.cursor.write_text(json.dumps({str(self.board): "legwork-2#2"}))
        self.script.save_states({str(self.cursor): {str(self.board): "legwork-2"}},
                                {str(self.cursor): {}})
        self.assertEqual(json.loads(self.cursor.read_text())[str(self.board)], "legwork-2#2")

    def test_an_id_ending_in_hash_digits_is_reported_once(self):
        self.write("## t#1 DECISION 2026-10-10 12:00\nAttempted: x\n\n")
        self.assertEqual(self.run_wait(), (0, ["legwork t#1 DECISION"]))
        self.assertEqual(self.run_wait(), (124, ["timeout"]))

    # A decision closed by "Replaces <id>" as the last words of a heading.
    def test_replaces_on_the_heading_line_closes_a_decision(self):
        self.write("## legwork-6 DECISION 2026-10-01 20:10\nAttempted: x\n\n"
                   "## legwork-9 DECISION 2026-10-01 21:21 Replaces legwork-6\n"
                   "Attempted: y\n\n")
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-9 DECISION"]))

    def test_replaces_in_heading_prose_does_not_close_a_decision(self):
        self.write(RECIPES["recipe-box-17"],
                   "## recipe-box-18 UPDATE 2026-09-25 19:30 new loader Replaces "
                   "recipe-box-17 style\nProse.\n\n")
        self.assertEqual(self.run_wait(), (0, ["legwork recipe-box-17 DECISION"]))

    # An answer typed into the pane: the DECIDED entry carries Replaces.
    def test_decided_with_replaces_closes_a_decision(self):
        self.write("## legwork-80 DECISION 2026-10-01 16:54\nAttempted: x\n\n"
                   "## legwork-82 DECIDED 2026-10-01 16:57\nReplaces legwork-80\n"
                   "The user said in the pane: \"after the tests\".\n\n")
        self.assertEqual(self.run_wait(), (124, ["timeout"]))

    def test_status_says_when_no_wait_watches_the_repo(self):
        self.write(BOARD["legwork-150"])
        self.assertEqual(self.status(), ["legwork wait none"])

    def test_a_finished_wait_takes_itself_off_the_list(self):
        self.write(BOARD["legwork-150"])
        listed = []
        real_update = self.script.update_pids

        def update(paths, add):
            real_update(paths, add)
            listed.append(json.loads(self.pids.read_text()))

        with mock.patch.object(self.script, "update_pids", update):
            self.assertEqual(self.run_wait(), (124, ["timeout"]))
        self.assertEqual([list(d) for d in listed], [[str(os.getpid())], []])

    def test_status_ignores_a_dead_wait_left_in_the_list(self):
        self.write(BOARD["legwork-150"])
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.pids.write_text(json.dumps({str(dead.pid): time.time()}))
        self.assertEqual(self.status(), ["legwork wait none"])

    def test_status_ignores_a_live_pid_that_is_not_a_wait(self):
        self.write(BOARD["legwork-150"])
        self.pids.write_text(json.dumps({str(os.getpid()): time.time()}))
        self.assertEqual(self.status(), ["legwork wait none"])

    # On Windows os.kill(pid, 0) ends the process, so it must never be
    # called there: with no ps to read either, a listed pid counts as gone.
    def test_windows_never_signals_a_listed_pid(self):
        with mock.patch.object(self.script.os, "name", "nt"), \
                mock.patch.object(self.script.os, "kill",
                                  side_effect=AssertionError("os.kill on Windows")):
            self.assertFalse(self.script.wait_alive(os.getpid(), self.script.STARTED))

    def test_a_held_lock_gives_up_after_its_limit(self):
        lock = str(self.tmp / "x.lock")
        with self.script.exclusive(lock):
            start = time.time()
            with self.assertRaises(OSError):
                with self.script.exclusive(lock, limit=0.5):
                    pass
            self.assertLess(time.time() - start, 3)

    # A new run was dropped at once because the latest entry was the DONE of
    # the run before, five days older than the watch.
    def test_done_before_the_run_started_is_not_reported_on_start(self):
        self.write(OLD_DONE)
        started(self.state_file, self.repo, "2026-10-10 23:49")
        self.assertEqual(self.run_wait(), (124, ["timeout"]))

    def test_cycle_before_the_run_started_is_not_reported_on_start(self):
        self.write("## legwork-100 CYCLE 2026-10-05 19:31\nContext full.\n\n")
        started(self.state_file, self.repo, "2026-10-10 23:49")
        self.assertEqual(self.run_wait(), (124, ["timeout"]))

    def test_done_after_the_run_started_is_reported(self):
        self.write(OLD_DONE, NEW_DONE)
        started(self.state_file, self.repo, "2026-10-10 23:49")
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-101 DONE"]))

    def test_done_in_the_same_minute_as_the_start_is_reported(self):
        self.write("## legwork-101 DONE 2026-10-10 23:49\nDone.\n\n")
        started(self.state_file, self.repo, "2026-10-10 23:49")
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-101 DONE"]))

    def test_done_written_during_the_wait_is_reported(self):
        self.write(OLD_DONE)
        started(self.state_file, self.repo, "2026-10-10 23:49")
        t = threading.Timer(0.5, self.append, [NEW_DONE])
        t.start()
        try:
            rc, lines = self.main(["--timeout", "10", f"{self.repo}:none"])
        finally:
            t.cancel()
        self.assertEqual((rc, lines), (0, ["legwork legwork-101 DONE"]))

    def test_old_done_is_reported_when_the_repo_has_no_started_line(self):
        self.write(OLD_DONE)
        started(self.state_file, self.repo, None)
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-100 DONE"]))

    def test_a_started_line_for_another_repo_does_not_apply(self):
        self.write(OLD_DONE)
        started(self.state_file, self.tmp / "recipe-box", "2026-10-10 23:49",
                project="recipe-box")
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-100 DONE"]))

    def test_open_decision_before_the_run_started_is_still_reported(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        started(self.state_file, self.repo, "2026-10-10 23:49")
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-151 DECISION"]))

    def test_status_leaves_out_a_done_before_the_run_started(self):
        self.write(OLD_DONE)
        started(self.state_file, self.repo, "2026-10-10 23:49")
        self.assertEqual(self.status(), ["legwork wait none"])

    def test_status_keeps_a_done_after_the_run_started(self):
        self.write(OLD_DONE, NEW_DONE)
        started(self.state_file, self.repo, "2026-10-10 23:49")
        self.assertEqual(self.status(), ["legwork legwork-101 DONE Roadmap 4 complete.",
                                         "legwork wait none"])

    def test_state_option_names_the_state_file(self):
        self.write(OLD_DONE)
        other = self.tmp / "elsewhere.md"
        started(other, self.repo, "2026-10-10 23:49")
        state = ["--state", str(other)]
        self.assertEqual(self.main(["--status", *state, f"{self.repo}:none"])[1],
                         ["legwork wait none"])
        self.assertEqual(self.main(["--timeout", "1", *state, f"{self.repo}:none"]),
                         (124, ["timeout"]))

    def test_a_missing_state_file_keeps_the_old_behaviour(self):
        self.write(OLD_DONE)
        missing = str(self.tmp / "none.md")
        self.assertEqual(self.main(["--status", "--state", missing, f"{self.repo}:none"])[1],
                         ["legwork legwork-100 DONE Roadmap 3 complete.", "legwork wait none"])

    def test_state_option_without_a_file_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.main(["--status", "--state"])[0], 2)

    @unittest.skipUnless(os.name == "nt", "Windows paths ignore case and slash direction")
    def test_a_windows_repo_path_matches_however_it_is_spelled(self):
        self.write(OLD_DONE)
        started(self.state_file, str(self.repo).upper().replace("\\", "/"), "2026-10-10 23:49")
        self.assertEqual(self.run_wait(), (124, ["timeout"]))


OLD_DONE = "## legwork-100 DONE 2026-10-05 19:31\nRoadmap 3 complete.\n\n"
NEW_DONE = "## legwork-101 DONE 2026-10-11 00:42\nRoadmap 4 complete.\n\n"


def started(state_file, repo, when, project="legwork"):
    """Write the switchboard's state file as switchboard-state does: watching
    `repo` as `project`, with this run started at `when` (None: no line)."""
    text = ("# switchboard state\nupdated: 2026-10-10 23:56\n## watching\n"
            f"{project} {repo} w1:p2\n## started\n")
    if when:
        text += f"{project} {when}\n"
    Path(state_file).write_text(text + f"## check-ins\n{project} 2026-10-10 23:56\n")


class EarlierRunPaneTests(SwitchboardCase):
    """A stopped pane makes the wait report a latest CYCLE or DONE on every
    start; one from before the run started still stays out."""

    def test_done_before_the_run_started_is_left_out_while_the_pane_is_done(self):
        self.write(OLD_DONE)
        started(self.state_file, self.repo, "2026-10-10 23:49")
        self.herdr("done")
        self.assertEqual(self.run_wait()[:2], (0, ["legwork pane done"]))

    def test_cycle_before_the_run_started_is_left_out_while_the_pane_is_idle(self):
        self.write("## legwork-100 CYCLE 2026-10-05 19:31\nContext full.\n\n")
        started(self.state_file, self.repo, "2026-10-10 23:49")
        self.herdr("idle")
        self.assertEqual(self.run_wait()[:2], (0, ["legwork pane idle"]))

    def test_done_in_the_same_minute_is_reported_with_the_pane(self):
        self.write("## legwork-101 DONE 2026-10-10 23:49\nDone.\n\n")
        started(self.state_file, self.repo, "2026-10-10 23:49")
        self.herdr("done")
        self.assertEqual(self.run_wait()[:2],
                         (0, ["legwork legwork-101 DONE", "legwork pane done"]))


DECISION = "## legwork-200 DECISION 2026-10-20 09:00\nAttempted: x\n\n"


class PaneFlipTests(unittest.TestCase):
    """Pane states that wake the switchboard, with herdr faked in process, so
    these run on Windows too. A pane that only flips between idle and done
    did nothing new, and while the latest entry is an open DECISION already
    reported, the orchestrator is waiting on the user."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.script = load_script()
        self.script.USAGE_CACHE = str(self.tmp / "no-usage.json")
        self.script.POLL = 0.2
        self.script.STATE_FILE = str(self.tmp / "state.md")
        self.repo = self.tmp / "legwork"
        self.board = self.repo / ".legwork" / "switchboard.md"
        self.board.parent.mkdir(parents=True)
        self.board.write_text(BOARD["legwork-150"])
        self.pane = ("working", 1)
        self.script.pane_status = lambda pane, deadline: self.pane

    def set_pane(self, status, seq):
        self.pane = (status, seq)

    def append(self, text):
        with open(self.board, "a") as f:
            f.write(text)

    def main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.script.main(argv)
        return rc, out.getvalue().splitlines()

    def run_wait(self, *changes, timeout=1):
        """One wait; each change is (delay, function, args) run while it waits."""
        timers = [threading.Timer(d, f, a) for d, f, a in changes]
        for t in timers:
            t.start()
        try:
            return self.main(["--timeout", str(timeout), f"{self.repo}:w1:p2"])
        finally:
            for t in timers:
                t.cancel()

    def test_idle_to_done_between_waits_is_not_an_event(self):
        self.set_pane("idle", 1)
        self.assertEqual(self.run_wait(), (0, ["legwork pane idle"]))
        self.set_pane("done", 2)
        self.assertEqual(self.run_wait(), (124, ["timeout"]))
        self.set_pane("idle", 3)
        self.assertEqual(self.run_wait(), (124, ["timeout"]))

    def test_flips_during_a_wait_are_not_events(self):
        self.set_pane("done", 1)
        self.assertEqual(self.run_wait(), (0, ["legwork pane done"]))
        self.assertEqual(self.run_wait((0.3, self.set_pane, ["idle", 2]),
                                       (0.6, self.set_pane, ["done", 3])),
                         (124, ["timeout"]))

    def test_working_then_stopped_is_still_an_event(self):
        self.set_pane("idle", 1)
        self.assertEqual(self.run_wait(), (0, ["legwork pane idle"]))
        self.assertEqual(self.run_wait((0.3, self.set_pane, ["working", 2]),
                                       (0.6, self.set_pane, ["done", 3]), timeout=10),
                         (0, ["legwork pane done"]))

    def test_unknown_then_idle_is_an_event(self):
        self.set_pane("unknown", 1)
        self.assertEqual(self.run_wait(), (124, ["timeout"]))
        self.set_pane("idle", 2)
        self.assertEqual(self.run_wait(), (0, ["legwork pane idle"]))

    def test_blocked_and_gone_after_idle_are_still_events(self):
        self.set_pane("idle", 1)
        self.assertEqual(self.run_wait(), (0, ["legwork pane idle"]))
        self.set_pane("blocked", 2)
        self.assertEqual(self.run_wait(), (0, ["legwork pane blocked"]))
        self.set_pane("gone", None)
        self.assertEqual(self.run_wait(), (0, ["legwork pane gone"]))

    def test_an_open_decision_already_reported_quiets_idle_and_done(self):
        self.append(DECISION)
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-200 DECISION"]))
        self.set_pane("idle", 2)
        self.assertEqual(self.run_wait(), (124, ["timeout"]))
        self.assertEqual(self.run_wait((0.3, self.set_pane, ["working", 3]),
                                       (0.6, self.set_pane, ["done", 4])),
                         (124, ["timeout"]))

    def test_an_open_decision_does_not_quiet_blocked(self):
        self.append(DECISION)
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-200 DECISION"]))
        self.set_pane("blocked", 2)
        self.assertEqual(self.run_wait(), (0, ["legwork pane blocked"]))

    def test_an_answered_decision_brings_pane_reports_back(self):
        self.append(DECISION)
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-200 DECISION"]))
        self.set_pane("idle", 2)
        self.assertEqual(self.run_wait(), (124, ["timeout"]))
        self.append("ANSWERED legwork-200: b (2026-10-20 09:30)\n\n")
        self.assertEqual(self.run_wait((0.3, self.set_pane, ["working", 3]),
                                       (0.6, self.set_pane, ["idle", 4]), timeout=10),
                         (0, ["legwork pane idle"]))

    def test_a_decided_that_replaces_it_brings_pane_reports_back(self):
        self.append(DECISION)
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-200 DECISION"]))
        self.append("## legwork-201 DECIDED 2026-10-20 09:40\nReplaces legwork-200\n"
                    "The user said in the pane: \"b\".\n\n")
        self.assertEqual(self.run_wait((0.3, self.set_pane, ["idle", 2]), timeout=10),
                         (0, ["legwork pane idle"]))

    def test_a_new_decision_that_replaces_it_is_reported(self):
        self.append(DECISION)
        self.set_pane("idle", 2)
        self.assertEqual(self.run_wait()[0], 0)
        self.append("## legwork-202 DECISION 2026-10-20 10:00 Replaces legwork-200\n"
                    "Attempted: y\n\n")
        self.assertEqual(self.run_wait(), (0, ["legwork legwork-202 DECISION"]))
        self.set_pane("done", 3)
        self.assertEqual(self.run_wait(), (124, ["timeout"]))

    def test_status_still_shows_the_pane_while_a_decision_is_open(self):
        self.append(DECISION)
        self.set_pane("idle", 2)
        self.run_wait()
        self.assertEqual(self.main(["--status", f"{self.repo}:w1:p2"]),
                         (0, ["legwork legwork-200 DECISION Attempted: x",
                              "legwork pane idle", "legwork wait none"]))


class StateFileDefaultTests(unittest.TestCase):
    """Where the state file is when --state is not given."""

    def load(self, **env):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("SWITCHBOARD_STATE", "LEGWORK_SWITCHBOARD_DIR",
                              "CLAUDE_CONFIG_DIR", "LEGWORK_CONFIG")}
        # No legwork config of the machine's own may answer for the setting.
        clean["LEGWORK_DIR"] = tmp.name
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            return load_script().STATE_FILE

    def test_the_switchboard_dir_setting(self):
        self.assertEqual(self.load(LEGWORK_SWITCHBOARD_DIR="/sb", CLAUDE_CONFIG_DIR="/cc"),
                         os.path.join("/sb", "state.md"))

    def test_the_claude_config_dir_without_the_setting(self):
        self.assertEqual(self.load(CLAUDE_CONFIG_DIR="/cc"),
                         os.path.join("/cc", "switchboard", "state.md"))

    def test_the_home_claude_dir_without_either(self):
        self.assertEqual(self.load(), os.path.join(os.path.expanduser("~/.claude"),
                                                   "switchboard", "state.md"))

    def test_switchboard_state_overrides_the_setting(self):
        self.assertEqual(self.load(SWITCHBOARD_STATE="/x/state.md",
                                   LEGWORK_SWITCHBOARD_DIR="/sb"), "/x/state.md")


if __name__ == "__main__":
    unittest.main()
