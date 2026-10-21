"""Tests for orchestration/bin/switchboard-health-ctl.

The script runs in process with its call() replaced by a fake, so the suite
runs on Windows, macOS and Linux and never reaches a real launchctl or Task
Scheduler:

  - launchctl records every call and answers `print`, `bootstrap` and
    `bootout` with exit codes each test sets.
  - schtasks.exe keeps one registered task in memory: /Create stores the
    task XML it is given, /Query finds it (or, with /XML, prints it back),
    /Delete drops it, and any of them fails with a code a test sets.

Which form runs is the script's WINDOWS switch, so both forms are tested on
every platform. The dry run is the real switchboard-health, run with this
Python against the temp state directory, where it finds no state file and
does nothing. HOME and the Claude config dir point into a temp directory,
so nothing touches the real ~/Library/LaunchAgents or a real switchboard
folder.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "orchestration" / "bin"
SCRIPT = BIN / "switchboard-health-ctl"
HEALTH = BIN / "switchboard-health"
TEMPLATE = HERE.parent / "orchestration" / "launchd" / "dev.legwork.switchboard-health.plist.template"
LABEL = "dev.legwork.switchboard-health"
DOMAIN = "gui/501"
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
CLEAN_ENV = ("CLAUDE_CONFIG_DIR", "LEGWORK_DIR", "LEGWORK_CONFIG", "LEGWORK_SESSION_ARGS",
             "LEGWORK_SWITCHBOARD_DIR", "SWITCHBOARD_STATE")


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def done(argv, code=0, out="", err=""):
    return subprocess.CompletedProcess(argv, code, out, err)


class FakeTools:
    """launchctl and schtasks.exe, in process. The dry run goes to the real script."""

    def __init__(self, mod):
        self.mod = mod
        self.calls = []
        self.codes = {}       # launchctl subcommand or schtasks verb -> exit code
        self.error = "failed"
        self.task = None      # the registered task's XML, as schtasks would hold it

    def __call__(self, argv, timeout=60):
        argv = [str(a) for a in argv]
        if argv[0] == "launchctl":
            self.calls.append(argv[1:])
            rc = self.codes.get(argv[1], 0)
            if rc:
                return done(argv, rc, "", self.error)
            out = f"{DOMAIN}/{LABEL} = {{\n\tstate = waiting\n}}" if argv[1] == "print" else ""
            return done(argv, 0, out)
        if argv[0] == "schtasks.exe":
            return self.schtasks(argv)
        if len(argv) > 1 and argv[1] == self.mod.HEALTH:
            self.calls.append(["dry-run"] + argv[2:])
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        raise AssertionError(f"unexpected command: {argv}")

    def schtasks(self, argv):
        args = argv[1:]
        self.calls.append(args)
        verb = args[0]
        rc = self.codes.get(verb, 0)
        if rc:
            return done(argv, rc, "", f"ERROR: {self.error}")
        name = args[args.index("/TN") + 1]
        if name != LABEL:
            return done(argv, 1, "", "ERROR: The system cannot find the file specified.")
        if verb == "/Create":
            path = args[args.index("/XML") + 1]
            with open(path, encoding="utf-16") as f:
                self.task = f.read()
            return done(argv, 0, f'SUCCESS: The scheduled task "{name}" has successfully been created.')
        if self.task is None:
            return done(argv, 1, "", "ERROR: The system cannot find the file specified.")
        if verb == "/Query":
            return done(argv, 0, self.task if "/XML" in args else f"{name}  N/A  Ready")
        if verb == "/Delete":
            self.task = None
            return done(argv, 0, f'SUCCESS: The scheduled task "{name}" was successfully deleted.')
        if verb == "/Run":
            return done(argv, 0, f'SUCCESS: Attempted to run the scheduled task "{name}".')
        raise AssertionError(f"unexpected schtasks call: {args}")

    def verbs(self):
        return [c[0] for c in self.calls]


class CtlCase(unittest.TestCase):
    windows = False

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in CLEAN_ENV:
            os.environ.pop(key, None)
        os.environ["PATH"] = str(self.bin)
        self.add_tool("herdr")
        self.claude_home = self.home / ".claude-alt"
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude_home)
        self.state_dir = self.claude_home / "switchboard"
        self.mod = load(SCRIPT, "switchboard_health_ctl")
        self.tools = FakeTools(self.mod)
        self.patch("call", self.tools)
        self.patch("WINDOWS", self.windows)
        self.patch("home", lambda: str(self.home))
        self.patch("uid", lambda: 501)
        self.patch("login_mark", lambda: "111")
        self.out = self.err = ""

    def patch(self, name, value):
        p = mock.patch.object(self.mod, name, value)
        p.start()
        self.addCleanup(p.stop)

    def run_ctl(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.mod.main(list(args))
        self.out, self.err = out.getvalue(), err.getvalue()
        return rc

    def add_tool(self, name, where=None):
        """A stand-in program the script can find; never run."""
        d = where or self.bin
        d.mkdir(parents=True, exist_ok=True)
        c = d / (name + ".exe" if os.name == "nt" else name)
        c.write_text("#!/bin/sh\n", encoding="utf-8")
        c.chmod(0o755)
        return d

    def add_claude(self, where=None):
        return self.add_tool("claude", where)

    def on_path(self, *folders):
        os.environ["PATH"] = os.pathsep.join([os.environ["PATH"]] + [str(f) for f in folders])

    def drop_herdr(self):
        for c in self.bin.glob("herdr*"):
            c.unlink()

    def log_line(self, **over):
        rec = {"time": "2026-10-10T19:20:05+0100",
               "switchboard": {"pane": "w1:p1", "status": "idle"},
               "observed": [], "actions": [], "cleared": [], "notes": [], "errors": []}
        rec.update(over)
        return json.dumps(rec, sort_keys=True)

    def write_log(self, *lines):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "health.jsonl").write_text("".join(line + "\n" for line in lines),
                                                     encoding="utf-8")


class MacOnTests(CtlCase):

    @property
    def plist(self):
        return self.state_dir / f"{LABEL}.plist"

    def loaded_plist(self, path=None):
        return plistlib.loads((path or self.plist).read_bytes())

    def test_on_renders_the_plist_into_the_state_dir_and_bootstraps_it(self):
        self.tools.codes["print"] = 113  # not loaded
        claude_dir = self.add_claude()
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        plist = self.loaded_plist()
        self.assertEqual(plist["Label"], LABEL)
        self.assertEqual([os.path.normpath(a) for a in plist["ProgramArguments"]],
                         [os.path.normpath(a) for a in (sys.executable, str(HEALTH), "--once",
                                                        "--state-dir", str(self.state_dir))])
        self.assertTrue(plist["EnvironmentVariables"]["PATH"].startswith(
            f"{claude_dir}:/opt/homebrew/bin:"), plist["EnvironmentVariables"]["PATH"])
        self.assertEqual(plist["StartInterval"], 300)
        self.assertIs(plist["RunAtLoad"], True)
        self.assertEqual(os.path.normpath(plist["StandardOutPath"]),
                         str(self.state_dir / "health.out.log"))
        self.assertEqual(os.path.normpath(plist["StandardErrorPath"]),
                         str(self.state_dir / "health.err.log"))
        self.assertNotIn("@", self.plist.read_text(encoding="utf-8").split("-->", 1)[1])
        self.assertIn(["bootstrap", DOMAIN, str(self.plist)], self.tools.calls)

    def test_on_passes_the_claude_config_dir_in_the_job_environment(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        os.environ["LEGWORK_DIR"] = str(self.tmp / "queue")
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        env = self.loaded_plist()["EnvironmentVariables"]
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.claude_home))
        self.assertEqual(env["LEGWORK_DIR"], str(self.tmp / "queue"))
        self.assertNotIn("LEGWORK_CONFIG", env)

    def test_on_puts_the_herdr_and_notifier_folders_on_the_job_path(self):
        self.tools.codes["print"] = 113
        claude_dir = self.add_claude()
        self.drop_herdr()
        herdr_dir = self.add_tool("herdr", self.tmp / "herdr-home")
        notifier_dir = self.add_tool("terminal-notifier", self.tmp / "notifier-home")
        self.on_path(herdr_dir, notifier_dir)
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        path = self.loaded_plist()["EnvironmentVariables"]["PATH"]
        self.assertTrue(path.startswith(":".join([str(claude_dir), str(herdr_dir),
                                                  str(notifier_dir), "/opt/homebrew/bin:"])),
                        path)

    def test_on_without_herdr_is_an_error_and_loads_nothing(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        self.drop_herdr()
        self.assertEqual(self.run_ctl("on"), 1)
        self.assertIn("herdr", self.err)
        self.assertNotIn("bootstrap", self.tools.verbs())
        self.assertFalse(self.plist.exists())

    def test_on_passes_switchboard_state_in_the_job_environment(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        os.environ["SWITCHBOARD_STATE"] = str(self.tmp / "boards" / "state.md")
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        env = self.loaded_plist()["EnvironmentVariables"]
        self.assertEqual(env["SWITCHBOARD_STATE"], str(self.tmp / "boards" / "state.md"))

    def test_on_without_a_claude_config_dir_leaves_it_unset(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        del os.environ["CLAUDE_CONFIG_DIR"]
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        plist = self.home / ".claude" / "switchboard" / f"{LABEL}.plist"
        self.assertNotIn("CLAUDE_CONFIG_DIR", self.loaded_plist(plist)["EnvironmentVariables"])

    def test_on_escapes_paths_for_the_plist(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        odd = self.tmp / "a&b"
        self.assertEqual(self.run_ctl("on", "--state-dir", str(odd)), 0, self.err)
        plist = self.loaded_plist(odd / f"{LABEL}.plist")
        self.assertEqual(os.path.normpath(plist["StandardOutPath"]), str(odd / "health.out.log"))

    def test_on_creates_the_state_dir(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        self.assertFalse(self.state_dir.exists())
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        self.assertTrue(self.state_dir.is_dir())

    def test_on_shows_the_dry_run_before_it_bootstraps(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        # The dry run is the real script; with no state file it notes that and sends nothing.
        self.assertIn("no state file", self.out)
        self.assertFalse((self.state_dir / "health.jsonl").exists())
        self.assertLess(self.out.index("no state file"), self.out.index("switched on"))
        self.assertLess(self.tools.verbs().index("dry-run"), self.tools.verbs().index("bootstrap"))

    def test_a_failed_dry_run_does_not_switch_on(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        self.patch("HEALTH", str(self.tmp / "no-such-script"))
        self.assertEqual(self.run_ctl("on"), 1)
        self.assertNotIn("bootstrap", self.tools.verbs())

    def test_on_with_state_dir_flag_uses_it_everywhere(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        other = self.tmp / "elsewhere"
        self.assertEqual(self.run_ctl("on", "--state-dir", str(other)), 0, self.err)
        plist = other / f"{LABEL}.plist"
        self.assertTrue(plist.exists())
        self.assertFalse(self.plist.exists())
        self.assertEqual(os.path.normpath(self.loaded_plist(plist)["StandardOutPath"]),
                         str(other / "health.out.log"))
        self.assertEqual(self.loaded_plist(plist)["ProgramArguments"][-1], str(other))
        self.assertIn(["bootstrap", DOMAIN, str(plist)], self.tools.calls)

    def test_on_never_writes_to_library_launchagents(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        self.assertFalse((self.home / "Library" / "LaunchAgents").exists())

    def test_on_when_already_loaded_says_so_and_changes_nothing(self):
        self.add_claude()
        self.assertEqual(self.run_ctl("on"), 0)
        self.assertIn("already on", self.out)
        self.assertEqual(self.tools.verbs(), ["print"])
        self.assertFalse(self.plist.exists())

    @unittest.skipIf(os.name == "nt", "nvm keeps POSIX bin folders")
    def test_on_finds_claude_under_nvm_when_it_is_not_on_path(self):
        self.tools.codes["print"] = 113
        nvm = self.add_claude(self.home / ".nvm" / "versions" / "node" / "v20.20.0" / "bin")
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        path = self.loaded_plist()["EnvironmentVariables"]["PATH"]
        self.assertEqual(path.split(":")[:2], [str(nvm), str(self.bin)])

    @unittest.skipIf(os.name == "nt", "nvm keeps POSIX bin folders")
    def test_on_prefers_the_newest_nvm_node(self):
        self.tools.codes["print"] = 113
        self.add_claude(self.home / ".nvm" / "versions" / "node" / "v18.2.0" / "bin")
        new = self.add_claude(self.home / ".nvm" / "versions" / "node" / "v20.20.0" / "bin")
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        path = self.loaded_plist()["EnvironmentVariables"]["PATH"]
        self.assertEqual(path.split(":")[0], str(new))

    def test_on_without_claude_is_an_error_and_loads_nothing(self):
        self.tools.codes["print"] = 113
        self.assertEqual(self.run_ctl("on"), 1)
        self.assertIn("claude", self.err)
        self.assertNotIn("bootstrap", self.tools.verbs())
        self.assertFalse(self.plist.exists())

    def test_on_reports_a_failed_bootstrap(self):
        self.tools.codes.update({"print": 113, "bootstrap": 5})
        self.tools.error = "Bootstrap failed: 5: Input/output error"
        self.add_claude()
        self.assertEqual(self.run_ctl("on"), 1)
        self.assertIn("Input/output error", self.err)
        self.assertNotIn("switched on", self.out)

    def test_mac_form_never_calls_schtasks(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        self.run_ctl("on")
        self.run_ctl("status")
        self.run_ctl("off")
        self.assertFalse(any(v.startswith("/") for v in self.tools.verbs()))


class MacOffAndStatusTests(CtlCase):

    @property
    def old_plist(self):
        return self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def test_off_boots_out_the_job_when_loaded(self):
        self.assertEqual(self.run_ctl("off"), 0, self.err)
        self.assertIn(["bootout", f"{DOMAIN}/{LABEL}"], self.tools.calls)
        self.assertIn("switched off", self.out)

    def test_off_when_not_loaded_says_so_and_exits_0(self):
        self.tools.codes["print"] = 113
        self.assertEqual(self.run_ctl("off"), 0)
        self.assertIn("already off", self.out)
        self.assertNotIn("bootout", self.tools.verbs())

    def test_off_reports_a_failed_bootout(self):
        self.tools.codes["bootout"] = 36
        self.tools.error = "Boot-out failed: 36"
        self.assertEqual(self.run_ctl("off"), 1)
        self.assertIn("Boot-out failed", self.err)

    def test_status_loaded_with_a_quiet_last_pass(self):
        self.write_log(self.log_line(time="2026-10-10T19:10:00+0100"), self.log_line())
        self.assertEqual(self.run_ctl("status"), 0, self.err)
        self.assertIn("on", self.out.splitlines()[0])
        self.assertIn("2026-10-10 19:20", self.out)
        self.assertIn("no alerts", self.out)

    def test_status_names_the_alerts_of_the_last_pass(self):
        self.write_log(self.log_line(
            observed=[{"repo": "/src/legwork", "pane": "w1:p2", "condition": "stall", "count": 2}],
            actions=[{"kind": "prompt", "ok": True}],
            errors=["herdr agent list failed"]))
        self.assertEqual(self.run_ctl("status"), 0)
        self.assertIn("1 alert", self.out)
        self.assertIn("stall", self.out)
        self.assertIn("herdr agent list failed", self.out)

    def test_status_not_loaded(self):
        self.tools.codes["print"] = 113
        self.assertEqual(self.run_ctl("status"), 0)
        self.assertIn("off", self.out.splitlines()[0])

    def test_status_without_a_log_says_no_pass_yet(self):
        self.assertEqual(self.run_ctl("status"), 0)
        self.assertIn("no pass", self.out)

    def test_status_survives_a_torn_last_line(self):
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "health.jsonl").write_text(self.log_line() + "\n" + '{"time": "2026-10',
                                                     encoding="utf-8")
        self.assertEqual(self.run_ctl("status"), 0)
        self.assertIn("unreadable", self.out)

    def test_on_warns_about_an_old_login_plist_and_never_deletes_it(self):
        self.tools.codes["print"] = 113
        self.add_claude()
        self.old_plist.parent.mkdir(parents=True)
        self.old_plist.write_text("old", encoding="utf-8")
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        text = self.out + self.err
        self.assertIn("at login", text)
        self.assertIn(f"rm {shlex.quote(str(self.old_plist))}", text)
        self.assertEqual(self.old_plist.read_text(encoding="utf-8"), "old")

    def test_status_warns_about_an_old_login_plist(self):
        self.tools.codes["print"] = 113
        self.old_plist.parent.mkdir(parents=True)
        self.old_plist.write_text("old", encoding="utf-8")
        self.assertEqual(self.run_ctl("status"), 0)
        self.assertIn("at login", self.out + self.err)
        self.assertTrue(self.old_plist.exists())

    def test_off_does_not_warn_or_delete(self):
        self.tools.codes["print"] = 113
        self.old_plist.parent.mkdir(parents=True)
        self.old_plist.write_text("old", encoding="utf-8")
        self.assertEqual(self.run_ctl("off"), 0)
        self.assertTrue(self.old_plist.exists())
        self.assertNotIn("at login", self.out + self.err)

    def test_no_warning_without_an_old_plist(self):
        self.tools.codes["print"] = 113
        self.run_ctl("status")
        self.assertNotIn("at login", self.out + self.err)


class WindowsTests(CtlCase):
    windows = True

    @property
    def xml_path(self):
        return self.state_dir / f"{LABEL}.xml"

    def task(self):
        """The registered task: (root element, command, arguments as a list)."""
        self.assertIsNotNone(self.tools.task, "no task registered")
        root = ET.fromstring(self.tools.task.encode("utf-16"))
        command = root.find("t:Actions/t:Exec/t:Command", TASK_NS).text
        arguments = root.find("t:Actions/t:Exec/t:Arguments", TASK_NS).text
        return root, command, arguments

    def register_earlier(self, mark):
        """A task left registered by an earlier sign-in."""
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        self.tools.task = self.tools.task.replace("--login 111", f"--login {mark}")
        self.tools.calls.clear()

    def test_on_registers_a_task_from_xml_kept_in_the_state_dir(self):
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        create = [c for c in self.tools.calls if c[0] == "/Create"]
        self.assertEqual(create, [["/Create", "/TN", LABEL, "/XML", str(self.xml_path)]])
        self.assertTrue(self.xml_path.exists())
        self.assertTrue(self.xml_path.read_bytes().startswith(b"\xff\xfe"), "UTF-16 with a BOM")
        self.assertIn("switched on", self.out)

    def test_task_runs_every_five_minutes_only_while_signed_in(self):
        self.run_ctl("on")
        root, _, _ = self.task()
        trigger = root.find("t:Triggers/t:TimeTrigger", TASK_NS)
        self.assertEqual(trigger.find("t:Repetition/t:Interval", TASK_NS).text, "PT5M")
        self.assertIsNone(trigger.find("t:Repetition/t:Duration", TASK_NS), "repeats with no end")
        self.assertEqual(root.find("t:Principals/t:Principal/t:LogonType", TASK_NS).text,
                         "InteractiveToken")
        self.assertEqual(root.find("t:Principals/t:Principal/t:RunLevel", TASK_NS).text,
                         "LeastPrivilege")
        settings = root.find("t:Settings", TASK_NS)
        self.assertEqual(settings.find("t:MultipleInstancesPolicy", TASK_NS).text, "IgnoreNew")
        self.assertEqual(settings.find("t:DisallowStartIfOnBatteries", TASK_NS).text, "false")
        self.assertEqual(settings.find("t:StopIfGoingOnBatteries", TASK_NS).text, "false")

    def test_task_runs_the_watcher_with_this_interpreter(self):
        self.patch("interpreter", lambda: r"C:\Py\pythonw.exe")
        self.run_ctl("on")
        _, command, arguments = self.task()
        self.assertEqual(command, r"C:\Py\pythonw.exe")
        self.assertEqual(arguments, subprocess.list2cmdline([
            str(HEALTH), "--once", "--state-dir", str(self.state_dir),
            "--env", f"CLAUDE_CONFIG_DIR={self.claude_home}", "--path", str(self.bin),
            "--login", "111"]))

    def test_task_passes_the_legwork_settings_it_finds(self):
        os.environ["LEGWORK_DIR"] = str(self.tmp / "queue")
        os.environ["LEGWORK_SESSION_ARGS"] = "--model opus --effort high"
        self.run_ctl("on")
        _, _, arguments = self.task()
        self.assertIn(subprocess.list2cmdline(["--env", f"LEGWORK_DIR={self.tmp / 'queue'}"]),
                      arguments)
        self.assertIn(subprocess.list2cmdline(
            ["--env", "LEGWORK_SESSION_ARGS=--model opus --effort high"]), arguments)

    def test_task_passes_switchboard_state(self):
        os.environ["SWITCHBOARD_STATE"] = str(self.tmp / "boards" / "state.md")
        self.run_ctl("on")
        _, _, arguments = self.task()
        self.assertIn(subprocess.list2cmdline(
            ["--env", f"SWITCHBOARD_STATE={self.tmp / 'boards' / 'state.md'}"]), arguments)

    def test_task_puts_the_herdr_folder_on_its_path(self):
        self.drop_herdr()
        herdr_dir = self.add_tool("herdr", self.tmp / "herdr home")
        self.on_path(herdr_dir)
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        _, _, arguments = self.task()
        self.assertIn(subprocess.list2cmdline(["--path", str(herdr_dir)]), arguments)

    def test_on_without_herdr_is_an_error_and_registers_nothing(self):
        self.drop_herdr()
        self.assertEqual(self.run_ctl("on"), 1)
        self.assertIn("herdr", self.err)
        self.assertNotIn("/Create", self.tools.verbs())
        self.assertIsNone(self.tools.task)

    def test_task_without_a_claude_config_dir_passes_none(self):
        del os.environ["CLAUDE_CONFIG_DIR"]
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        _, _, arguments = self.task()
        self.assertNotIn("CLAUDE_CONFIG_DIR", arguments)

    def test_task_without_a_readable_sign_in_carries_no_login(self):
        self.patch("login_mark", lambda: None)
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        _, _, arguments = self.task()
        self.assertNotIn("--login", arguments)
        self.assertIn("sign-in", self.out)

    def test_task_xml_escapes_odd_paths(self):
        odd = self.tmp / "a&b"
        self.assertEqual(self.run_ctl("on", "--state-dir", str(odd)), 0, self.err)
        self.assertIn("a&amp;b", self.tools.task)
        root, _, arguments = self.task()
        self.assertIn(str(odd), arguments)
        self.assertEqual(root.find("t:Actions/t:Exec/t:WorkingDirectory", TASK_NS).text, str(odd))

    def test_on_shows_the_dry_run_then_runs_one_pass_now(self):
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        verbs = self.tools.verbs()
        self.assertLess(verbs.index("dry-run"), verbs.index("/Create"))
        self.assertLess(verbs.index("/Create"), verbs.index("/Run"))
        self.assertIn("no state file", self.out)
        self.assertFalse((self.state_dir / "health.jsonl").exists())

    def test_a_failed_run_now_still_leaves_it_on(self):
        self.tools.codes["/Run"] = 1
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        self.assertIsNotNone(self.tools.task)
        self.assertIn("first pass", self.out + self.err)

    def test_on_when_already_on_says_so_and_changes_nothing(self):
        self.run_ctl("on")
        self.tools.calls.clear()
        self.assertEqual(self.run_ctl("on"), 0)
        self.assertIn("already on", self.out)
        self.assertNotIn("/Create", self.tools.verbs())

    def test_on_replaces_a_task_left_from_an_earlier_sign_in(self):
        self.register_earlier("99")
        self.assertEqual(self.run_ctl("on"), 0, self.err)
        verbs = self.tools.verbs()
        self.assertLess(verbs.index("/Delete"), verbs.index("/Create"))
        self.assertIn("--login 111", self.task()[2])

    def test_on_reports_a_failed_create(self):
        self.tools.codes["/Create"] = 1
        self.tools.error = "Access is denied."
        self.assertEqual(self.run_ctl("on"), 1)
        self.assertIn("Access is denied", self.err)
        self.assertNotIn("switched on", self.out)
        self.assertNotIn("/Run", self.tools.verbs())

    def test_on_needs_no_claude_on_path(self):
        self.assertEqual(self.run_ctl("on"), 0, self.err)

    def test_off_deletes_the_task(self):
        self.run_ctl("on")
        self.tools.calls.clear()
        self.assertEqual(self.run_ctl("off"), 0, self.err)
        self.assertIn(["/Delete", "/TN", LABEL, "/F"], self.tools.calls)
        self.assertIsNone(self.tools.task)
        self.assertIn("switched off", self.out)

    def test_off_also_deletes_a_task_from_an_earlier_sign_in(self):
        self.register_earlier("99")
        self.assertEqual(self.run_ctl("off"), 0, self.err)
        self.assertIsNone(self.tools.task)

    def test_off_when_not_registered_says_so(self):
        self.assertEqual(self.run_ctl("off"), 0)
        self.assertIn("already off", self.out)
        self.assertNotIn("/Delete", self.tools.verbs())

    def test_off_reports_a_failed_delete(self):
        self.run_ctl("on")
        self.tools.codes["/Delete"] = 1
        self.tools.error = "Access is denied."
        self.assertEqual(self.run_ctl("off"), 1)
        self.assertIn("Access is denied", self.err)

    def test_status_on_and_off(self):
        self.run_ctl("on")
        self.write_log(self.log_line())
        self.assertEqual(self.run_ctl("status"), 0)
        self.assertIn("on", self.out.splitlines()[0])
        self.assertIn("2026-10-10 19:20", self.out)
        self.run_ctl("off")
        self.assertEqual(self.run_ctl("status"), 0)
        self.assertIn("off", self.out.splitlines()[0])

    def test_status_counts_a_task_from_an_earlier_sign_in_as_off(self):
        self.register_earlier("99")
        self.assertEqual(self.run_ctl("status"), 0)
        self.assertIn("off", self.out.splitlines()[0])
        self.assertIn("earlier sign-in", self.out)

    def test_windows_form_never_calls_launchctl_or_warns_about_plists(self):
        old = self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        old.parent.mkdir(parents=True)
        old.write_text("old", encoding="utf-8")
        self.run_ctl("on")
        self.run_ctl("status")
        self.run_ctl("off")
        self.assertFalse(any(v in ("print", "bootstrap", "bootout") for v in self.tools.verbs()))
        self.assertNotIn("at login", self.out + self.err)

    def test_interpreter_prefers_the_windowless_python_beside_this_one(self):
        folder = self.tmp / "py"
        folder.mkdir()
        (folder / "python.exe").write_text("", encoding="utf-8")
        with mock.patch.object(self.mod.sys, "executable", str(folder / "python.exe")):
            self.assertEqual(self.mod.interpreter(), str(folder / "python.exe"))
            (folder / "pythonw.exe").write_text("", encoding="utf-8")
            self.assertEqual(self.mod.interpreter(), str(folder / "pythonw.exe"))


class UsageTests(CtlCase):

    def test_help_runs_on_this_python(self):
        done = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("on|off|status", done.stdout)

    def test_no_command_is_a_usage_error(self):
        self.assertEqual(self.run_ctl(), 2)
        self.assertIn("Usage", self.err)

    def test_unknown_command_is_a_usage_error(self):
        self.assertEqual(self.run_ctl("restart"), 2)
        self.assertEqual(self.tools.calls, [])

    def test_same_commands_on_both_platforms(self):
        for windows in (False, True):
            with self.subTest(windows=windows), mock.patch.object(self.mod, "WINDOWS", windows):
                self.assertEqual(self.run_ctl("status"), 0)
                self.assertEqual(self.run_ctl("bogus"), 2)

    def test_default_state_dir_follows_the_legwork_setting(self):
        os.environ["LEGWORK_SWITCHBOARD_DIR"] = str(self.tmp / "board")
        self.assertEqual(self.mod.default_state_dir(), str(self.tmp / "board"))

    @unittest.skipIf(os.name == "nt", "the executable bit is a POSIX idea")
    def test_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_template_has_the_generic_label_and_no_machine_paths(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(f"<string>{LABEL}</string>", text)
        self.assertNotIn("/Users/", text)


if __name__ == "__main__":
    unittest.main()
