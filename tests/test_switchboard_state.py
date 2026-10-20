"""Tests for orchestration/bin/switchboard-state.

The script is the switchboard's only way to change its state file. Each test
points it at a temp file with --file and fixes the clock with
SWITCHBOARD_NOW, so the times written are known. It runs as a subprocess
through sys.executable, so these run on Windows too.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "orchestration" / "bin"
SCRIPT = BIN / "switchboard-state"
NOW = "2026-10-10 23:49"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class SwitchboardStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.file = self.tmp / "sb" / "state.md"

    def run_state(self, *args, now=NOW):
        env = dict(os.environ, SWITCHBOARD_NOW=now)
        done = subprocess.run([sys.executable, str(SCRIPT), "--file", str(self.file), *args],
                              capture_output=True, text=True, env=env)
        return done.returncode, done.stdout, done.stderr

    def text(self):
        return self.file.read_text(encoding="utf-8")

    def test_run_adds_watching_started_and_check_in_lines(self):
        rc, _, err = self.run_state("add", "--run", "legwork", "/src/legwork", "wAR:p5")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.text(), (
            "# switchboard state\nupdated: 2026-10-10 23:49\n"
            "## watching\nlegwork /src/legwork wAR:p5\n"
            "## started\nlegwork 2026-10-10 23:49\n"
            "## check-ins\nlegwork 2026-10-10 23:49\n"))

    def test_the_file_is_written_with_lf_line_endings(self):
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2")
        self.assertNotIn(b"\r", self.file.read_bytes())

    def test_watch_without_run_writes_no_started_line(self):
        self.run_state("add", "recipe-box", "/src/recipe-box", "w1:p2")
        self.assertIn("## started\n## check-ins", self.text())

    def test_adding_again_keeps_the_first_start_and_updates_the_pane(self):
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2")
        self.run_state("add", "legwork", "/src/legwork", "w9:p1", now="2026-10-11 02:00")
        text = self.text()
        self.assertIn("legwork /src/legwork w9:p1\n", text)
        self.assertNotIn("legwork /src/legwork w1:p2", text)
        self.assertIn("## started\nlegwork 2026-10-10 23:49\n", text)
        self.assertIn("updated: 2026-10-11 02:00", text)

    def test_a_second_run_moves_the_start(self):
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2")
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2", now="2026-10-12 09:00")
        self.assertIn("## started\nlegwork 2026-10-12 09:00\n", self.text())

    def test_remove_takes_out_every_line_for_the_project(self):
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2")
        self.run_state("add", "--run", "recipe-box", "/src/recipe-box", "w2:p2")
        rc, _, _ = self.run_state("remove", "legwork")
        self.assertEqual(rc, 0)
        self.assertNotIn("legwork", self.text())
        self.assertIn("recipe-box /src/recipe-box w2:p2", self.text())

    def test_remove_of_the_last_project_leaves_an_empty_file(self):
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2")
        self.run_state("remove", "legwork", now="2026-10-11 00:43")
        self.assertEqual(self.text(), ("# switchboard state\nupdated: 2026-10-11 00:43\n"
                                       "## watching\n## started\n## check-ins\n"))

    def test_checkin_updates_each_named_project(self):
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2")
        self.run_state("add", "--run", "recipe-box", "/src/recipe-box", "w2:p2")
        self.run_state("checkin", "legwork", "recipe-box", now="2026-10-11 01:30")
        text = self.text()
        self.assertIn("## check-ins\nlegwork 2026-10-11 01:30\nrecipe-box 2026-10-11 01:30\n", text)
        self.assertIn("## started\nlegwork 2026-10-10 23:49\n", text)

    def test_pane_changes_only_the_pane(self):
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2")
        self.run_state("pane", "legwork", "w5:p1")
        self.assertIn("legwork /src/legwork w5:p1\n", self.text())
        self.assertIn("legwork 2026-10-10 23:49\n## check-ins", self.text())

    def test_unknown_project_is_refused_and_nothing_written(self):
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2")
        before = self.text()
        for args in (("remove", "nope"), ("checkin", "nope"), ("pane", "nope", "w1:p1")):
            rc, _, err = self.run_state(*args, now="2026-10-11 05:00")
            self.assertEqual(rc, 1, args)
            self.assertIn("nope", err)
        self.assertEqual(self.text(), before)

    def test_cycle_marks_the_last_line_and_uncycle_clears_it(self):
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2")
        self.run_state("cycle", now="2026-10-11 03:00")
        self.assertTrue(self.text().endswith("CYCLE 2026-10-11 03:00\n"))
        self.run_state("add", "recipe-box", "/src/recipe-box", "w2:p2", now="2026-10-11 03:05")
        self.assertTrue(self.text().endswith("CYCLE 2026-10-11 03:00\n"))
        self.run_state("uncycle", now="2026-10-11 03:10")
        self.assertNotIn("CYCLE", self.text())

    def test_repo_path_with_a_space_round_trips(self):
        self.run_state("add", "--run", "legwork", "/src/my legwork", "w1:p2")
        self.run_state("checkin", "legwork")
        self.assertIn("legwork /src/my legwork w1:p2\n", self.text())

    def test_a_windows_repo_path_round_trips(self):
        self.run_state("add", "--run", "legwork", r"E:\dev\projects\my legwork", "w1:p2")
        self.run_state("checkin", "legwork")
        self.assertIn("legwork E:\\dev\\projects\\my legwork w1:p2\n", self.text())

    def test_show_prints_the_file(self):
        self.run_state("add", "--run", "legwork", "/src/legwork", "w1:p2")
        rc, out, _ = self.run_state("show")
        self.assertEqual(rc, 0)
        self.assertEqual(out, self.text())

    def test_bad_arguments_exit_2(self):
        for args in ((), ("add", "legwork"), ("frob",), ("pane", "legwork")):
            rc, _, _ = self.run_state(*args)
            self.assertEqual(rc, 2, args)
        self.assertFalse(self.file.exists())

    def test_a_file_option_without_a_file_exits_2(self):
        done = subprocess.run([sys.executable, str(SCRIPT), "--file"],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)

    def test_project_names_with_spaces_are_refused(self):
        rc, _, _ = self.run_state("add", "--run", "two words", "/src/q", "w1:p2")
        self.assertEqual(rc, 2)

    def test_write_replaces_the_file_whole(self):
        mod = load("switchboard_state", SCRIPT)
        calls, real = [], os.replace

        def replace(a, b):
            calls.append((a, b))
            real(a, b)

        with mock.patch.object(mod.os, "replace", replace), \
                mock.patch.dict(os.environ, {"SWITCHBOARD_NOW": NOW}):
            self.assertEqual(mod.main(["--file", str(self.file), "add", "--run",
                                       "legwork", "/src/legwork", "w1:p2"]), 0)
        self.assertEqual(calls[0][1], str(self.file))

    # On Windows a rename onto a file another process has open (a wait
    # reading it) fails with PermissionError for a moment.
    def test_a_briefly_locked_file_is_retried(self):
        mod = load("switchboard_state", SCRIPT)
        mod.REPLACE_PAUSE = 0.01
        real, fails = os.replace, [PermissionError("in use"), PermissionError("in use")]

        def replace(a, b):
            if fails:
                raise fails.pop()
            real(a, b)

        with mock.patch.object(mod.os, "replace", replace), \
                mock.patch.dict(os.environ, {"SWITCHBOARD_NOW": NOW}):
            self.assertEqual(mod.main(["--file", str(self.file), "add",
                                       "legwork", "/src/legwork", "w1:p2"]), 0)
        self.assertIn("legwork /src/legwork w1:p2\n", self.text())
        self.assertEqual([p.name for p in self.file.parent.iterdir()], ["state.md"])

    def test_wait_reads_the_started_line_the_script_writes(self):
        wait = load("switchboard_wait", BIN / "switchboard-wait")
        repo = self.tmp / "legwork"
        self.run_state("add", "--run", "legwork", str(repo), "w1:p2")
        self.assertEqual(wait.started_at(str(repo), str(self.file)), NOW)

    @unittest.skipIf(os.name == "nt", "Windows has no executable bit")
    def test_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))


class DefaultFileTests(unittest.TestCase):
    """Where the state file is without --file: the same place switchboard-wait reads."""

    def default(self, **env):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("SWITCHBOARD_STATE", "LEGWORK_SWITCHBOARD_DIR",
                              "CLAUDE_CONFIG_DIR", "LEGWORK_CONFIG")}
        clean["LEGWORK_DIR"] = tmp.name  # no machine config may answer
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            state = load("switchboard_state", SCRIPT).default_file()
            wait = load("switchboard_wait", BIN / "switchboard-wait").STATE_FILE
        self.assertEqual(state, wait)
        return state

    def test_the_switchboard_dir_setting(self):
        self.assertEqual(self.default(LEGWORK_SWITCHBOARD_DIR="/sb", CLAUDE_CONFIG_DIR="/cc"),
                         os.path.join("/sb", "state.md"))

    def test_the_claude_config_dir_without_the_setting(self):
        self.assertEqual(self.default(CLAUDE_CONFIG_DIR="/cc"),
                         os.path.join("/cc", "switchboard", "state.md"))

    def test_the_home_claude_dir_without_either(self):
        self.assertEqual(self.default(), os.path.join(os.path.expanduser("~/.claude"),
                                                      "switchboard", "state.md"))

    def test_switchboard_state_overrides_the_setting(self):
        self.assertEqual(self.default(SWITCHBOARD_STATE="/x/state.md",
                                      LEGWORK_SWITCHBOARD_DIR="/sb"), "/x/state.md")


if __name__ == "__main__":
    unittest.main()
