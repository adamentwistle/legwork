"""Tests for orchestration/bin/switchboard-health.

The script runs in process with its call() replaced by a fake, so the suite
runs on Windows, macOS and Linux and nothing reaches a real pane:

  - herdr answers `agent list` and `agent get` from one list of agents each
    test sets, records every call, and fails a subcommand with a herdr error
    code when a test asks it to. Real herdr 0.9.0 shapes (checked with
    `herdr agent list` on Windows): the agent's session id is
    agent_session.value, and an agent has a name only once one is given.
  - the banner (terminal-notifier, or the Windows toast through
    powershell.exe) records its arguments and exits with a code the test sets.
  - switchboard-health-ctl records its arguments, so switching the watcher
    off never reaches launchctl or schtasks.
  - `switchboard-wait --status` is the real script, run in process, its pane
    lookups answered by the same fake herdr, except where a test needs a
    wait to show as running.

Every run passes --state-dir, so nothing touches a real switchboard folder.
"""
import base64
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "orchestration" / "bin"
SCRIPT = BIN / "switchboard-health"
WAIT = BIN / "switchboard-wait"
T0 = 1791650000.0  # 2026-10-10, any fixed time will do
MIN = 60
NOTIFIER = "/fake/bin/terminal-notifier"
POWERSHELL = "C:/fake/powershell.exe"
# Settings the script reads, cleared so the machine running the tests
# cannot leak into them.
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


class FakeWorld:
    """herdr, the banner, switchboard-health-ctl and switchboard-wait, in process."""

    def __init__(self, mod, wait):
        self.mod, self.wait = mod, wait
        self.agents, self.failing = [], {}
        self.root_pane, self.clear_takes = "wS:p1", True
        self.herdr_calls, self.banner_calls, self.ctl_calls = [], [], []
        self.banner_code, self.running = 0, False
        self.banner_err = "Could not show the notification"

    def __call__(self, argv, timeout, env=None):
        argv = list(argv)
        if argv[0] == "herdr":
            return self.herdr(argv[1:])
        if argv[0] in (NOTIFIER, POWERSHELL):
            self.banner_calls.append((argv, env))
            return done(argv, self.banner_code, "", self.banner_err if self.banner_code else "")
        if len(argv) > 1 and argv[1] == self.mod.CTL:
            self.ctl_calls.append(argv[2:])
            return done(argv)
        if len(argv) > 1 and argv[1] == self.mod.WAIT:
            return self.wait_status(argv)
        raise AssertionError(f"unexpected command: {argv}")

    def herdr(self, args):
        self.herdr_calls.append(args)
        key = " ".join(args[:2])
        if key in self.failing:
            # Real herdr prints errors as JSON on stderr and exits 1.
            return done(args, 1, "", json.dumps({"error": {"code": self.failing[key]}}))
        if key == "agent list":
            return done(args, 0, json.dumps({"id": "cli:agent:list", "result": {
                "type": "agent_list", "agents": self.agents}}))
        if key == "agent get":
            a = self.find(args[2])
            if a is None:
                return done(args, 1, "", json.dumps({"error": {"code": "agent_not_found"}}))
            return done(args, 0, json.dumps({"id": "cli:agent:get", "result": {
                "type": "agent_info", "agent": {
                    "agent_status": a["agent_status"],
                    "state_change_seq": a.get("state_change_seq", 1),
                    "agent_session": {"kind": "id", "value": a.get("session", "s0")},
                    "pane_id": a["pane_id"]}}}))
        if key == "agent prompt" and args[3] == "/clear" and self.clear_takes:
            # /clear starts a new Claude Code session, so herdr reports a new session id.
            a = self.find(args[2])
            if a is not None:
                a["session"] = a.get("session", "s0") + "+"
        if key == "workspace create":
            return done(args, 0, json.dumps({"id": "cli:workspace:create", "result": {
                "type": "workspace_created", "workspace": {"workspace_id": "wS"},
                "tab": {"tab_id": "wS:t1"}, "root_pane": {"pane_id": self.root_pane}}}))
        return done(args, 0, json.dumps({"id": "cli", "result": {}}))

    def find(self, pane):
        for a in self.agents:
            if pane in (a.get("pane_id"), a.get("name")):
                return a
        return None

    def pane_status(self, pane, deadline):
        reply = self.herdr(["agent", "get", pane])
        body = json.loads(reply.stdout or reply.stderr)
        if "result" not in body:
            return ("gone" if body["error"]["code"] == "agent_not_found" else "unreachable", None)
        agent = body["result"]["agent"]
        return agent["agent_status"], agent.get("state_change_seq")

    def wait_status(self, argv):
        target = argv[-1]
        if self.running:
            # The one state the real script cannot be put in cheaply: a live
            # wait listed in the pids file.
            repo, _ = self.wait.split_target(target)
            name = os.path.basename(repo)
            return done(argv, 0, f"{name} pane working\n"
                                 f"{name} wait running since 18:00 (pid 4242)\n")
        out = io.StringIO()
        with mock.patch.object(self.wait, "pane_status", self.pane_status), \
                contextlib.redirect_stdout(out):
            rc = self.wait.main(argv[2:])
        return done(argv, rc, out.getvalue())


class HealthCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in CLEAN_ENV:
            os.environ.pop(key, None)
        self.mod = load(SCRIPT, "switchboard_health")
        self.world = FakeWorld(self.mod, load(WAIT, "switchboard_wait_for_health"))
        self.patch("call", self.world)
        self.patch("NOTIFIER", NOTIFIER)
        self.patch("POWERSHELL", POWERSHELL)
        self.patch("WINDOWS", False)
        self.patch("ROOT", "/legwork")
        self.patch("CLEAR_TIMEOUT", 0.3)
        self.patch("CLEAR_POLL", 0.05)
        self.patch("login_mark", lambda: None)
        self.dir = self.tmp / "sb"
        self.dir.mkdir()
        self.log_path = self.dir / "health.jsonl"
        self.state_path = self.dir / "health-state.json"
        self.repo = self.tmp / "legwork"
        self.repo2 = self.tmp / "recipe-box"
        for r in (self.repo, self.repo2):
            (r / ".legwork").mkdir(parents=True)
            self.board(r, f"## {r.name}-1 UPDATE 2026-10-10 18:00\nStarted.\n\n")
        self.out = self.err = ""

    def tearDown(self):
        # Never Esc, never keys of any kind: the switchboard's wait must live.
        for c in self.world.herdr_calls:
            self.assertNotIn("send-keys", c)

    def patch(self, name, value):
        p = mock.patch.object(self.mod, name, value)
        p.start()
        self.addCleanup(p.stop)

    def board(self, repo, text):
        (repo / ".legwork" / "switchboard.md").write_text(text, encoding="utf-8")

    def agents(self, *agents):
        self.world.agents = [dict(a) for a in agents]

    def fail(self, key, code):
        self.world.failing[key] = code

    def heal(self):
        self.world.failing = {}

    def state(self, watching=(), cycle=False, when="2026-10-10 18:40", path=None):
        lines = ["# switchboard state", "updated: 2026-10-10 18:40", "## watching"]
        lines += [f"{p} {r} {pane}" for p, r, pane in watching]
        lines += ["## check-ins"] + [f"{p} 2026-10-10 18:40" for p, _, _ in watching]
        if cycle:
            lines.append(f"CYCLE {when}")
        (path or self.dir / "state.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def watch(self, *repos):
        rows = {"legwork": (self.repo, "w1:p2"), "recipe-box": (self.repo2, "w2:p2")}
        self.state([(r, str(rows[r][0]), rows[r][1]) for r in repos])

    def run_pass(self, *extra, at=T0):
        self.world.herdr_calls, self.world.banner_calls, self.world.ctl_calls = [], [], []
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(self.mod, "now", lambda: at), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.mod.main(["--once", "--state-dir", str(self.dir)] + list(extra))
        self.out, self.err = out.getvalue(), err.getvalue()
        return rc

    def every_five(self, first, last):
        """Passes every 5 minutes from minute `first` to `last`, both included.
        Returns the prompts and banners of each, as {minute: (prompts, banners)}."""
        seen = {}
        for m in range(first, last + 1, 5):
            self.run_pass(at=T0 + m * MIN)
            seen[m] = (self.prompt_texts(), self.banners())
        return seen

    def prompts(self):
        return [(c[2], c[3]) for c in self.world.herdr_calls if c[:2] == ["agent", "prompt"]]

    def prompt_texts(self, prefix=""):
        return [t for _, t in self.prompts() if t.startswith(prefix)]

    def restart_calls(self):
        """Every herdr call that is not a read."""
        return [c for c in self.world.herdr_calls
                if c[:2] not in (["agent", "list"], ["agent", "get"])]

    def banners(self):
        texts = []
        for argv, env in self.world.banner_calls:
            if argv[0] == NOTIFIER:
                texts.append(argv[argv.index("-message") + 1])
            else:
                texts.append(env["LEGWORK_TOAST_TEXT"])
        return texts

    def ctl_calls(self):
        return self.world.ctl_calls

    def log(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]


def sw(status="idle", seq=1, pane="wB:p1", name="switchboard", title=None):
    a = {"agent": "claude", "agent_status": status, "state_change_seq": seq, "pane_id": pane}
    if name:
        a["name"] = name
    if title:
        a["title"] = title
    return a


def orch(status="working", seq=1, pane="w1:p2", name="legwork"):
    return {"agent": "claude", "agent_status": status, "state_change_seq": seq,
            "pane_id": pane, "name": name}


class CommandLineTests(HealthCase):

    def test_help_runs_on_this_python(self):
        done = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--once", done.stdout)
        self.assertIn("--dry-run", done.stdout)

    def test_without_once_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.mod.main(["--state-dir", str(self.dir)]), 2)

    def test_bad_env_flag_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.mod.main(["--once", "--env", "no-equals"]), 2)

    def test_default_state_dir_follows_the_claude_config_dir(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude-home")
        self.assertEqual(self.mod.default_state_dir(),
                         os.path.join(str(self.tmp / "claude-home"), "switchboard"))
        del os.environ["CLAUDE_CONFIG_DIR"]
        self.assertEqual(self.mod.default_state_dir(),
                         os.path.join(os.path.expanduser("~/.claude"), "switchboard"))

    def test_legwork_switchboard_dir_sets_the_state_dir(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude-home")
        os.environ["LEGWORK_SWITCHBOARD_DIR"] = str(self.tmp / "board")
        self.assertEqual(self.mod.default_state_dir(), str(self.tmp / "board"))

    def test_env_flag_sets_the_environment_for_the_pass(self):
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "shell")
        self.watch("legwork")
        self.agents(orch("working"))
        self.run_pass("--env", "CLAUDE_CONFIG_DIR=" + str(self.tmp / "job"))
        create = self.restart_calls()[0]
        self.assertIn("CLAUDE_CONFIG_DIR=" + str(self.tmp / "job"), create)

    def test_path_flag_puts_a_folder_first_on_path_for_the_pass(self):
        os.environ["PATH"] = "elsewhere"
        self.run_pass("--path", str(self.tmp / "herdr-home"))
        self.assertEqual(os.environ["PATH"].split(os.pathsep),
                         [str(self.tmp / "herdr-home"), "elsewhere"])

    def test_session_args_split_like_a_shell(self):
        os.environ["LEGWORK_SESSION_ARGS"] = "--add-dir '/work/my notes' --effort high"
        self.assertEqual(self.mod.session_args(),
                         ["--add-dir", "/work/my notes", "--effort", "high"])

    def test_session_args_keep_windows_backslashes(self):
        self.patch("WINDOWS", True)
        os.environ["LEGWORK_SESSION_ARGS"] = (
            r"""--model opus --add-dir E:\dev\shared --add-dir "C:\my notes" --effort 'very high'""")
        self.assertEqual(self.mod.session_args(),
                         ["--model", "opus", "--add-dir", r"E:\dev\shared",
                          "--add-dir", r"C:\my notes", "--effort", "very high"])

    @unittest.skipIf(os.name == "nt", "the executable bit is a POSIX idea")
    def test_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))


class StateFileTests(HealthCase):
    """The state file is $SWITCHBOARD_STATE when it is set, as for
    switchboard-state and switchboard-wait, else DIR/state.md."""

    def test_switchboard_state_names_the_state_file(self):
        other = self.tmp / "boards" / "state.md"
        other.parent.mkdir()
        os.environ["SWITCHBOARD_STATE"] = str(other)
        self.state([("legwork", str(self.repo), "w1:p2")], path=other)
        self.agents(sw("working"), orch("working"))
        seen = []
        real = self.world.wait_status
        self.world.wait_status = lambda argv: (seen.append(argv), real(argv))[1]
        self.run_pass()
        (argv,) = seen
        self.assertEqual(argv[3:5], ["--state", str(other)])
        (line,) = self.log()
        self.assertFalse(any("no state file" in n for n in line["notes"]), line["notes"])
        self.assertEqual(self.ctl_calls(), [])

    def test_restart_carries_the_switchboard_folder(self):
        self.watch("legwork")
        self.agents(orch("working"))
        self.run_pass()
        create = self.restart_calls()[0]
        self.assertEqual(create[create.index(f"LEGWORK_SWITCHBOARD_DIR={self.dir}") - 1], "--env")
        self.assertFalse(any(a.startswith("SWITCHBOARD_STATE=") for a in create))

    def test_restart_carries_switchboard_state_when_it_is_set(self):
        other = self.tmp / "boards" / "state.md"
        other.parent.mkdir()
        os.environ["SWITCHBOARD_STATE"] = str(other)
        self.state([("legwork", str(self.repo), "w1:p2")], path=other)
        self.agents(orch("working"))
        self.run_pass()
        create = self.restart_calls()[0]
        self.assertEqual(create[create.index(f"SWITCHBOARD_STATE={other}") - 1], "--env")


class PassRecordTests(HealthCase):

    def test_quiet_pass_logs_one_line_and_sends_nothing(self):
        self.watch("legwork")
        self.agents(sw("working"), orch("working"))
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual((self.prompts(), self.banners()), ([], []))
        (line,) = self.log()
        self.assertEqual(line["switchboard"], {"pane": "wB:p1", "status": "working"})
        self.assertEqual(line["actions"], [])
        self.assertIn("time", line)

    def test_one_json_line_per_pass(self):
        self.watch("legwork")
        self.agents(sw("working"), orch("working"))
        for i in range(3):
            self.run_pass(at=T0 + i * 5 * MIN)
        self.assertEqual(len(self.log()), 3)

    def test_files_are_written_with_lf(self):
        self.watch("legwork")
        self.agents(sw("working"), orch("working"))
        self.run_pass()
        self.assertNotIn(b"\r", self.log_path.read_bytes())
        self.assertNotIn(b"\r", self.state_path.read_bytes())

    def test_no_state_file_does_nothing_but_log(self):
        self.agents(orch("working"))
        self.assertEqual(self.run_pass(), 0)
        self.assertTrue(all(c[:2] == ["agent", "list"] for c in self.world.herdr_calls))
        self.assertEqual(self.banners(), [])
        (line,) = self.log()
        self.assertIn("no state file", " ".join(line["notes"]))

    def test_herdr_unreachable_banners_once(self):
        self.watch("legwork")
        self.agents(sw("idle"))
        self.fail("agent list", "server_unavailable")
        self.run_pass()
        self.assertEqual(len(self.banners()), 1)
        self.assertTrue(self.log()[-1]["errors"])
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.banners(), [])

    def test_herdr_not_installed_is_an_error_not_a_crash(self):
        def missing(argv, timeout, env=None):
            raise FileNotFoundError(2, "No such file", argv[0])

        self.watch("legwork")
        self.patch("call", missing)
        self.assertEqual(self.run_pass(), 0)
        self.assertIn("could not run herdr", " ".join(self.log()[-1]["errors"]))


class SwitchboardPaneTests(HealthCase):
    """Check 1: the switchboard pane."""

    def test_switchboard_found_by_name_before_title(self):
        self.watch("legwork")
        self.agents(sw("working", pane="wX:p9", name=None, title="switchboard"),
                    sw("blocked", pane="wB:p1"), orch("working"))
        self.run_pass()
        self.assertEqual(self.log()[-1]["switchboard"], {"pane": "wB:p1", "status": "blocked"})

    def test_switchboard_found_by_title_when_no_name(self):
        self.watch("legwork")
        self.agents(sw("working", pane="wX:p9", name=None, title="switchboard"),
                    orch("working"))
        self.run_pass()
        self.assertEqual(self.log()[-1]["switchboard"], {"pane": "wX:p9", "status": "working"})
        self.assertFalse(any(c[:2] == ["workspace", "create"] for c in self.world.herdr_calls))

    def test_switchboard_found_by_terminal_title_as_herdr_shows_it(self):
        self.watch("legwork")
        a = sw("working", pane="wX:p9", name=None)
        a["terminal_title"] = "\u2733 switchboard"
        a["terminal_title_stripped"] = "switchboard"
        self.agents(a, orch("working"))
        self.run_pass()
        self.assertEqual(self.log()[-1]["switchboard"], {"pane": "wX:p9", "status": "working"})

    def test_blocked_switchboard_banners_once_until_it_clears(self):
        self.watch("legwork")
        self.agents(sw("blocked"), orch("working"))
        self.run_pass()
        (msg,) = self.banners()
        self.assertIn("blocked", msg)
        self.assertEqual(self.prompts(), [])
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.banners(), [])
        self.agents(sw("working"), orch("working"))
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.banners(), [])
        self.agents(sw("blocked"), orch("working"))
        self.run_pass(at=T0 + 15 * MIN)
        self.assertEqual(len(self.banners()), 1)

    def test_failed_banner_is_logged_and_retried(self):
        self.watch("legwork")
        self.agents(sw("blocked"), orch("working"))
        self.world.banner_code = 3
        self.run_pass()
        self.assertEqual(len(self.banners()), 1)
        (action,) = [a for a in self.log()[-1]["actions"] if a["kind"] == "banner"]
        self.assertIs(action["ok"], False)
        self.assertIn("3", action["error"])
        self.assertIn("no banner", self.err)
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(len(self.banners()), 1, "a failed banner leaves the fingerprint open")
        self.world.banner_code = 0
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(len(self.banners()), 1)
        self.assertEqual([a["ok"] for a in self.log()[-1]["actions"]], [True])
        self.run_pass(at=T0 + 15 * MIN)
        self.assertEqual(self.banners(), [])

    def test_missing_notifier_is_a_failed_banner(self):
        self.watch("legwork")
        self.agents(sw("blocked"), orch("working"))
        self.patch("NOTIFIER", None)
        self.run_pass()
        (action,) = self.log()[-1]["actions"]
        self.assertEqual(action["kind"], "banner")
        self.assertIs(action["ok"], False)
        self.assertIn("terminal-notifier", action["error"])
        self.assertIn("no banner", self.err)

    def test_mac_banner_goes_through_terminal_notifier(self):
        self.watch("legwork")
        self.agents(sw("blocked"), orch("working"))
        self.run_pass()
        ((argv, _),) = self.world.banner_calls
        self.assertEqual(argv[:3], [NOTIFIER, "-title", "legwork switchboard"])


class WindowsBannerTests(HealthCase):
    """On Windows the banner is a toast shown through powershell.exe."""

    def setUp(self):
        super().setUp()
        self.patch("WINDOWS", True)
        self.watch("legwork")
        self.agents(sw("blocked"), orch("working"))

    def test_toast_goes_through_powershell_with_the_text_in_its_environment(self):
        self.run_pass()
        ((argv, env),) = self.world.banner_calls
        self.assertEqual(argv[0], POWERSHELL)
        self.assertIn("-NoProfile", argv)
        self.assertIn("-NonInteractive", argv)
        script = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
        self.assertIn("ToastNotificationManager", script)
        self.assertIn("$env:LEGWORK_TOAST_TEXT", script)
        self.assertNotIn("blocked", script, "the text never goes into the script itself")
        self.assertIn("blocked", env["LEGWORK_TOAST_TEXT"])
        self.assertEqual(env["LEGWORK_TOAST_TITLE"], "legwork switchboard")
        self.assertIs(self.log()[-1]["actions"][0]["ok"], True)

    def test_toast_needs_no_module(self):
        self.run_pass()
        ((argv, _),) = self.world.banner_calls
        script = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
        self.assertNotIn("Import-Module", script)
        self.assertNotIn("Install-Module", script)

    def test_failed_toast_is_a_failed_banner_with_a_note(self):
        self.world.banner_code = 1
        self.run_pass()
        (action,) = self.log()[-1]["actions"]
        self.assertIs(action["ok"], False)
        self.assertIn("no banner", self.err)

    def test_failed_toast_error_is_plain_words_not_clixml(self):
        # Windows PowerShell 5.1 writes stderr as CLIXML when it is not a
        # console, a progress record first, as seen live.
        self.world.banner_code = 1
        self.world.banner_err = (
            '#< CLIXML\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/'
            '2004/04"><Obj S="progress" RefId="0"><TN RefId="0"><T>System.Management.Automation'
            '.PSCustomObject</T><T>System.Object</T></TN><MS><I64 N="SourceId">1</I64><PR N="Record">'
            '<AV>Preparing modules for first use.</AV><AI>0</AI><Nil /><PI>-1</PI><PC>-1</PC>'
            '<T>Completed</T><SR>-1</SR><SD> </SD></PR></MS></Obj>'
            '<S S="Error">The notification platform is unavailable &amp; off._x000D__x000A_</S>'
            '<S S="Error">At line:9 char:1_x000D__x000A_</S></Objs>')
        self.run_pass()
        (action,) = self.log()[-1]["actions"]
        self.assertIn("The notification platform is unavailable & off.", action["error"])
        self.assertNotIn("CLIXML", action["error"])
        self.assertNotIn("_x000D_", action["error"])

    def test_toast_hides_powershell_progress(self):
        self.run_pass()
        ((argv, _),) = self.world.banner_calls
        script = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
        self.assertIn("$ProgressPreference = 'SilentlyContinue'", script)

    def test_no_powershell_shows_nothing_and_notes_it(self):
        self.patch("POWERSHELL", None)
        self.run_pass()
        self.assertEqual(self.world.banner_calls, [])
        (action,) = self.log()[-1]["actions"]
        self.assertIs(action["ok"], False)
        self.assertIn("powershell.exe", action["error"])
        self.assertIn("no banner", self.err)


class CycleTests(HealthCase):
    """Check 2: the switchboard's own cycle."""

    def cycling(self, **kw):
        self.state([("legwork", str(self.repo), "w1:p2")], cycle=True, **kw)

    def test_cycle_marker_with_idle_switchboard_sends_three_prompts(self):
        self.cycling()
        self.agents(sw("idle"), orch("working"))
        self.run_pass()
        self.assertEqual(self.prompts(), [("wB:p1", "/clear"), ("wB:p1", "/rename switchboard"),
                                          ("wB:p1", "/switchboard resume")])

    def test_cycle_rename_waits_for_clear_to_take(self):
        self.cycling()
        self.agents(sw("idle"), orch("working"))
        self.run_pass()
        calls = self.world.herdr_calls
        clear = calls.index(["agent", "prompt", "wB:p1", "/clear"])
        rename = calls.index(["agent", "prompt", "wB:p1", "/rename switchboard"])
        between = calls[clear + 1:rename]
        self.assertIn(["agent", "get", "wB:p1"], between, "the new session is confirmed first")
        self.assertIn(["agent", "rename", "wB:p1", "switchboard"], between)

    def test_cycle_stops_when_clear_does_not_take(self):
        self.cycling()
        self.agents(sw("idle"), orch("working"))
        self.world.clear_takes = False
        self.run_pass()
        self.assertEqual(self.prompts(), [("wB:p1", "/clear")])
        (action,) = [a for a in self.log()[-1]["actions"] if a["kind"] == "cycle"]
        self.assertIs(action["ok"], False)
        self.assertIn("/clear", action["error"])

    def test_cycle_that_does_not_take_is_resent_once_then_banners(self):
        self.cycling()
        self.agents(sw("idle"), orch("working"))
        self.run_pass()
        self.assertEqual(len(self.prompts()), 3)
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.prompt_texts("/"), list(self.mod.CYCLE_PROMPTS), "resent once")
        self.assertEqual(self.banners(), [])
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.prompts(), [])
        (msg,) = self.banners()
        self.assertIn("CYCLE 2026-10-10 18:40", msg)
        self.assertIn("/switchboard resume", msg)
        self.run_pass(at=T0 + 15 * MIN)
        self.assertEqual((self.prompts(), self.banners()), ([], []))

    def test_cycle_resend_waits_for_idle(self):
        self.cycling()
        self.agents(sw("idle"), orch("working"))
        self.run_pass()
        self.agents(sw("working"), orch("working"))
        self.run_pass(at=T0 + 5 * MIN)
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual((self.prompts(), self.banners()), ([], []))

    def test_new_cycle_date_is_a_new_fingerprint(self):
        self.cycling()
        self.agents(sw("idle"), orch("working"))
        for i in range(3):
            self.run_pass(at=T0 + i * 5 * MIN)
        self.assertEqual(len(self.banners()), 1)
        self.cycling(when="2026-10-10 21:15")
        self.run_pass(at=T0 + 15 * MIN)
        self.assertEqual(len(self.prompts()), 3)

    def test_cycle_marker_counts_done_as_idle(self):
        self.cycling()
        self.agents(sw("done"), orch("working"))
        self.run_pass()
        self.assertEqual(self.prompt_texts("/"),
                         ["/clear", "/rename switchboard", "/switchboard resume"])

    def test_cycle_marker_waits_for_idle(self):
        self.cycling()
        self.agents(sw("working"), orch("working"))
        self.run_pass()
        self.assertEqual(self.prompts(), [])
        self.agents(sw("idle"), orch("working"))
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(len(self.prompts()), 3)

    def test_cycle_marker_must_be_the_last_line(self):
        self.watch("legwork")
        path = self.dir / "state.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("## check-ins", "CYCLE 2026-10-10 18:00\n## check-ins"),
                        encoding="utf-8")
        self.agents(sw("working"), orch("working"))
        self.run_pass()
        self.assertEqual(self.prompts(), [])
        self.assertFalse(any(o["condition"].startswith("cycle")
                             for o in self.log()[-1]["observed"]))

    def test_cycle_failure_is_retried(self):
        self.cycling()
        self.agents(sw("idle"), orch("working"))
        self.fail("agent prompt", "agent_blocked")
        self.run_pass()
        self.assertEqual([a["ok"] for a in self.log()[-1]["actions"]], [False])
        self.heal()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(len(self.prompts()), 3)


class WaitTests(HealthCase):
    """Check 3: the waits."""

    def test_wait_none_with_idle_switchboard_prompts_on_the_second_pass(self):
        self.watch("legwork")
        self.agents(sw("idle"), orch("working"))
        self.run_pass()
        self.assertEqual(self.prompts(), [])
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.prompts(), [("wB:p1", f"SWITCHBOARD HEALTH wait-none: {self.repo}")])
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.prompts(), [])

    def test_wait_none_two_pass_count_resets_when_switchboard_works(self):
        self.watch("legwork")
        self.agents(sw("idle"), orch("working"))
        self.run_pass()
        self.agents(sw("working"), orch("working"))
        self.run_pass(at=T0 + 5 * MIN)
        self.agents(sw("idle"), orch("working"))
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.prompts(), [])
        self.run_pass(at=T0 + 15 * MIN)
        self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH wait-none"),
                         [f"SWITCHBOARD HEALTH wait-none: {self.repo}"])

    def test_running_wait_is_not_wait_none(self):
        self.watch("legwork")
        self.world.running = True
        self.agents(sw("idle"), orch("working"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.prompts(), [])

    def test_wait_status_gets_the_state_file_and_the_repo_and_pane(self):
        self.watch("legwork")
        self.agents(sw("working"), orch("working"))
        seen = []
        real = self.world.wait_status
        self.world.wait_status = lambda argv: (seen.append(argv), real(argv))[1]
        self.run_pass()
        (argv,) = seen
        self.assertEqual(argv[2:6], ["--status", "--state", str(self.dir / "state.md"),
                                     f"{self.repo}:w1:p2"])

    def test_wait_none_fingerprint_is_per_repo(self):
        self.watch("legwork", "recipe-box")
        self.agents(sw("idle"), orch("working"), orch("working", pane="w2:p2", name="recipe-box"))
        self.run_pass()
        sent = []
        for i in (1, 2, 3):
            self.run_pass(at=T0 + i * 5 * MIN)
            sent.append(self.prompt_texts())
        self.assertEqual(sent, [[f"SWITCHBOARD HEALTH wait-none: {self.repo}"],
                                [f"SWITCHBOARD HEALTH wait-none: {self.repo2}"], []])

    def test_one_prompt_per_pass_the_rest_deferred(self):
        self.watch("legwork")
        self.agents(sw("idle"), orch("idle"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.prompt_texts(), [f"SWITCHBOARD HEALTH wait-none: {self.repo}"])
        (deferred,) = [a for a in self.log()[-1]["actions"] if a["ok"] is None]
        self.assertTrue(deferred["text"].startswith("SWITCHBOARD HEALTH check-in legwork"))
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.prompt_texts(),
                         ["SWITCHBOARD HEALTH check-in legwork: pane idle with no CYCLE or DONE"])

    def test_failed_prompt_does_not_use_up_the_pass(self):
        self.watch("legwork", "recipe-box")
        self.agents(sw("idle"), orch("working"), orch("working", pane="w2:p2", name="recipe-box"))
        self.run_pass()
        self.fail("agent prompt", "agent_blocked")
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(len(self.prompts()), 2, "both tried, neither landed")


class OrchestratorTests(HealthCase):
    """Check 4: the orchestrators."""

    def test_blocked_orchestrator_prompts_check_in_on_the_second_pass(self):
        self.watch("legwork")
        self.agents(sw("working"), orch("blocked"))
        self.run_pass()
        self.agents(sw("idle"), orch("blocked"))
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"),
                         ["SWITCHBOARD HEALTH check-in legwork: pane blocked"])
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"), [])

    def test_idle_orchestrator_without_cycle_or_done_prompts_check_in(self):
        self.watch("legwork")
        self.world.running = True
        self.agents(sw("idle"), orch("idle"))
        self.run_pass()
        self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"), [])
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"),
                         ["SWITCHBOARD HEALTH check-in legwork: pane idle with no CYCLE or DONE"])

    def test_idle_orchestrator_with_trailing_cycle_or_done_is_fine(self):
        for kind in ("CYCLE", "DONE"):
            with self.subTest(kind=kind):
                if self.state_path.exists():
                    self.state_path.unlink()
                self.watch("legwork")
                self.board(self.repo, "## legwork-1 UPDATE 2026-10-10 18:00\nStarted.\n\n"
                                      f"## legwork-2 {kind} 2026-10-10 19:00\nStopping here.\n\n")
                self.agents(sw("idle"), orch("done"))
                self.run_pass()
                self.run_pass(at=T0 + 5 * MIN)
                self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"), [])

    def test_repo_folder_with_a_space_in_its_name(self):
        spaced = self.tmp / "my project"
        (spaced / ".legwork").mkdir(parents=True)
        self.board(spaced, "## proj-1 UPDATE 2026-10-10 18:00\nStarted.\n\n"
                           "## proj-2 DONE 2026-10-10 19:00\nStopping here.\n\n")
        self.state([("proj", str(spaced), "w1:p2")])
        self.agents(sw("idle"), orch("done", name="proj"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.prompt_texts(), [f"SWITCHBOARD HEALTH wait-none: {spaced}"],
                         "wait none is seen, and the trailing DONE means no check-in")

    def test_done_from_an_earlier_run_does_not_hide_an_idle_orchestrator(self):
        # The latest entry was the DONE of the run before this one.
        self.watch("legwork")
        path = self.dir / "state.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "## check-ins", "## started\nlegwork 2026-10-10 23:49\n## check-ins"),
            encoding="utf-8")
        self.board(self.repo, "## legwork-100 DONE 2026-10-05 19:31\nRoadmap 3 complete.\n\n")
        self.agents(sw("idle"), orch("idle"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        observed = [o["condition"] for o in self.log()[-1]["observed"]]
        self.assertIn("check-in:pane idle with no CYCLE or DONE", observed)

    IDLE = "check-in:pane idle with no CYCLE or DONE"
    ASKED = ("## legwork-1 UPDATE 2026-10-10 18:00\nStarted.\n\n"
             "## legwork-2 DECISION 2026-10-10 19:00\nAttempted: x\nOptions: A or B\n\n")

    def observed(self):
        return [o["condition"] for o in self.log()[-1]["observed"]]

    def two_passes(self, orch_status="idle", start=0):
        self.agents(sw("idle"), orch(orch_status))
        self.run_pass(at=T0 + start * MIN)
        self.run_pass(at=T0 + (start + 5) * MIN)

    def test_idle_behind_an_open_decision_is_waiting_on_the_user(self):
        self.watch("legwork")
        self.board(self.repo, self.ASKED)
        self.two_passes()
        self.assertNotIn(self.IDLE, self.observed())
        self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"), [])

    def test_done_behind_an_open_decision_is_waiting_on_the_user(self):
        self.watch("legwork")
        self.board(self.repo, self.ASKED)
        self.two_passes("done")
        self.assertNotIn(self.IDLE, self.observed())

    def test_an_answered_decision_brings_the_idle_check_in_back(self):
        self.watch("legwork")
        self.board(self.repo, self.ASKED + "ANSWERED legwork-2: A (2026-10-10 19:10)\n\n")
        self.two_passes()
        self.assertIn(self.IDLE, self.observed())

    def test_a_later_update_brings_the_idle_check_in_back(self):
        self.watch("legwork")
        self.board(self.repo, self.ASKED + "## legwork-3 UPDATE 2026-10-10 19:20\nOn it.\n\n")
        self.two_passes()
        self.assertIn(self.IDLE, self.observed())

    def test_a_grant_after_the_decision_brings_the_idle_check_in_back(self):
        self.watch("legwork")
        self.board(self.repo, self.ASKED + '## legwork-g1 GRANT 2026-10-10 19:20\n'
                                           'User: "go on"\nScope: x\n\n')
        self.two_passes()
        self.assertIn(self.IDLE, self.observed())

    def test_blocked_behind_an_open_decision_still_prompts(self):
        self.watch("legwork")
        self.world.running = True
        self.board(self.repo, self.ASKED)
        self.two_passes("blocked")
        self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"),
                         ["SWITCHBOARD HEALTH check-in legwork: pane blocked"])

    def test_waiting_on_the_user_needs_the_decision_open_in_the_status(self):
        self.board(self.repo, self.ASKED)
        lines = ["legwork-2 DECISION Attempted: x", "pane idle", "wait none"]
        self.assertTrue(self.mod.waiting_on_user(str(self.repo), lines))
        self.assertFalse(self.mod.waiting_on_user(str(self.repo), lines[1:]))
        self.assertFalse(self.mod.waiting_on_user(str(self.tmp / "nowhere"), lines))

    def test_check_in_waits_for_an_idle_switchboard(self):
        self.watch("legwork")
        self.agents(sw("working"), orch("blocked"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.prompts(), [], "never prompt a busy switchboard")
        deferred = [a for a in self.log()[-1]["actions"] if a["kind"] == "prompt"]
        self.assertTrue(deferred)
        self.assertIsNone(deferred[0]["ok"])
        self.agents(sw("idle"), orch("blocked"))
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"),
                         ["SWITCHBOARD HEALTH check-in legwork: pane blocked"])

    def test_check_in_two_pass_count_resets_when_condition_clears(self):
        self.watch("legwork")
        self.world.running = True
        self.agents(sw("idle"), orch("blocked"))
        self.run_pass()
        self.agents(sw("idle"), orch("working"))
        self.run_pass(at=T0 + 5 * MIN)
        self.agents(sw("idle"), orch("blocked"))
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.prompts(), [])

    def test_check_in_fingerprint_reopens_after_clearing(self):
        self.watch("legwork")
        self.world.running = True
        self.agents(sw("idle"), orch("blocked"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertNotEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"), [])
        self.agents(sw("idle"), orch("working"))
        self.run_pass(at=T0 + 10 * MIN)
        self.agents(sw("idle"), orch("blocked"))
        self.run_pass(at=T0 + 15 * MIN)
        self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"), [])
        self.run_pass(at=T0 + 20 * MIN)
        self.assertEqual(self.prompt_texts("SWITCHBOARD HEALTH check-in"),
                         ["SWITCHBOARD HEALTH check-in legwork: pane blocked"])

    def test_stall_prompts_and_banners_after_sixty_minutes(self):
        self.watch("legwork")
        self.world.running = True
        self.agents(sw("idle"), orch("working", seq=7))
        seen = self.every_five(0, 70)
        self.assertTrue(all(seen[m] == ([], []) for m in range(0, 60, 5)))
        prompts, banners = seen[60]
        self.assertEqual(prompts, ["SWITCHBOARD HEALTH stall legwork: 60 min with no change"])
        (msg,) = banners
        self.assertIn("legwork", msg)
        self.assertEqual(seen[65], ([], []))
        self.assertEqual(seen[70], ([], []))

    def test_stall_clock_restarts_when_seq_moves(self):
        self.watch("legwork")
        self.world.running = True
        self.agents(sw("idle"), orch("working", seq=7))
        self.every_five(0, 25)
        self.agents(sw("idle"), orch("working", seq=8))
        seen = self.every_five(30, 90)
        self.assertTrue(all(seen[m][0] == [] for m in range(30, 90, 5)))
        self.assertEqual(seen[90][0], ["SWITCHBOARD HEALTH stall legwork: 60 min with no change"])

    def test_stall_clock_restarts_when_switchboard_file_changes(self):
        self.watch("legwork")
        self.world.running = True
        self.agents(sw("idle"), orch("working", seq=7))
        self.every_five(0, 25)
        board = self.repo / ".legwork" / "switchboard.md"
        with open(board, "a", encoding="utf-8") as f:
            f.write("## legwork-2 UPDATE 2026-10-10 19:00\nStill going.\n\n")
        os.utime(board, (T0 + 1000, T0 + 1000))
        seen = self.every_five(30, 90)
        self.assertTrue(all(seen[m][0] == [] for m in range(30, 90, 5)))
        self.assertEqual(len(seen[90][0]), 1)

    def test_stall_banner_goes_even_when_switchboard_is_busy(self):
        self.watch("legwork")
        self.agents(sw("working"), orch("working", seq=7))
        seen = self.every_five(0, 60)
        self.assertEqual(seen[60][0], [])
        self.assertEqual(len(seen[60][1]), 1)
        self.agents(sw("idle"), orch("working", seq=7))
        self.world.running = True
        self.run_pass(at=T0 + 65 * MIN)
        self.assertEqual(self.prompt_texts(),
                         ["SWITCHBOARD HEALTH stall legwork: 65 min with no change"])
        self.assertEqual(self.banners(), [])


class GapTests(HealthCase):
    """A long gap between passes: the computer slept, or the job did not run."""

    def test_stall_clock_restarts_after_a_long_gap(self):
        self.watch("legwork")
        self.world.running = True
        self.agents(sw("idle"), orch("working", seq=7))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.run_pass(at=T0 + 65 * MIN)
        self.assertEqual((self.prompts(), self.banners()), ([], []), "asleep is not stalled")
        self.assertTrue(any("gap" in n for n in self.log()[-1]["notes"]))
        for m in range(75, 125, 10):
            self.run_pass(at=T0 + m * MIN)
            self.assertEqual(self.prompts(), [])
        self.run_pass(at=T0 + 125 * MIN)
        self.assertEqual(self.prompt_texts(),
                         ["SWITCHBOARD HEALTH stall legwork: 60 min with no change"])

    def test_two_pass_count_restarts_after_a_long_gap(self):
        self.watch("legwork")
        self.world.running = True
        self.agents(sw("idle"), orch("blocked"))
        self.run_pass()
        self.run_pass(at=T0 + 30 * MIN)
        self.assertEqual(self.prompts(), [])
        self.run_pass(at=T0 + 35 * MIN)
        self.assertEqual(self.prompt_texts(), ["SWITCHBOARD HEALTH check-in legwork: pane blocked"])

    def test_fifteen_minutes_is_not_a_gap(self):
        self.watch("legwork")
        self.world.running = True
        self.agents(sw("idle"), orch("blocked"))
        self.run_pass()
        self.run_pass(at=T0 + 15 * MIN)
        self.assertEqual(self.prompt_texts(), ["SWITCHBOARD HEALTH check-in legwork: pane blocked"])

    def test_gap_keeps_sent_fingerprints(self):
        self.watch("legwork")
        self.agents(sw("blocked"), orch("working"))
        self.run_pass()
        self.run_pass(at=T0 + 60 * MIN)
        self.assertEqual(self.banners(), [], "blocked banner already sent, and asleep is not stalled")


class RestartTests(HealthCase):
    """Bringing back a switchboard that is gone."""

    def test_missing_switchboard_is_restarted(self):
        os.environ["CLAUDE_CONFIG_DIR"] = "/claude-home"
        self.watch("legwork")
        self.agents(orch("working"))
        self.run_pass()
        calls = self.restart_calls()
        self.assertEqual(calls[0], ["workspace", "create", "--cwd", "/legwork",
                                    "--label", "switchboard",
                                    "--env", "CLAUDE_CONFIG_DIR=/claude-home",
                                    "--env", f"LEGWORK_SWITCHBOARD_DIR={self.dir}",
                                    "--env", "DISABLE_AUTO_UPDATE=true", "--no-focus"])
        self.assertEqual(calls[1], ["pane", "rename", "wS:p1", "switchboard"])
        start = calls[2]
        self.assertEqual(start[:6], ["agent", "start", "switchboard", "--kind", "claude", "--pane"])
        self.assertEqual(start[6], "wS:p1")
        self.assertEqual(start[-5:], ["--", "--model", "opus", "--effort", "high"])
        self.assertEqual(self.prompts(), [("wS:p1", "/rename switchboard"),
                                          ("wS:p1", "/switchboard resume")])
        (action,) = [a for a in self.log()[-1]["actions"] if a["kind"] == "restart"]
        self.assertIs(action["ok"], True)
        self.assertEqual(action["pane"], "wS:p1")
        self.assertEqual(self.banners(), [])

    def test_restart_without_a_claude_config_dir_sets_none(self):
        self.watch("legwork")
        self.agents(orch("working"))
        self.run_pass()
        create = self.restart_calls()[0]
        self.assertFalse(any(a.startswith("CLAUDE_CONFIG_DIR=") for a in create))
        self.assertIn("DISABLE_AUTO_UPDATE=true", create)

    def test_restart_starts_in_legwork_dir_when_it_is_set(self):
        os.environ["LEGWORK_DIR"] = str(self.tmp / "queue")
        self.watch("legwork")
        self.agents(orch("working"))
        self.run_pass()
        create = self.restart_calls()[0]
        self.assertEqual(create[create.index("--cwd") + 1], str(self.tmp / "queue"))

    def test_restart_takes_its_session_args_from_the_setting(self):
        os.environ["LEGWORK_SESSION_ARGS"] = "--model sonnet --effort 'very high'"
        self.watch("legwork")
        self.agents(orch("working"))
        self.run_pass()
        start = self.restart_calls()[2]
        self.assertEqual(start[start.index("--"):], ["--", "--model", "sonnet",
                                                     "--effort", "very high"])

    def test_no_restart_when_watching_nothing(self):
        self.state([])
        self.agents(orch("working"))
        self.run_pass()
        self.assertEqual((self.restart_calls(), self.banners()), ([], []))
        self.assertTrue(any("watches nothing" in n for n in self.log()[-1]["notes"]))
        self.assertFalse(self.log()[-1]["actions"])

    def test_no_restart_without_a_state_file(self):
        self.agents(orch("working"))
        self.run_pass()
        self.assertEqual(self.restart_calls(), [])

    def test_watching_nothing_still_banners_a_blocked_switchboard(self):
        self.state([])
        self.agents(sw("blocked"))
        self.run_pass()
        self.assertEqual(len(self.banners()), 1)

    def test_restart_at_most_once_an_hour_then_banner(self):
        self.watch("legwork")
        self.agents(orch("working"))
        self.run_pass()
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.restart_calls(), [], "no second restart inside the hour")
        (msg,) = self.banners()
        self.assertIn("/switchboard resume", msg)
        self.run_pass(at=T0 + 20 * MIN)
        self.assertEqual((self.restart_calls(), self.banners()), ([], []))
        self.run_pass(at=T0 + 61 * MIN)
        self.assertEqual(self.restart_calls()[0][:2], ["workspace", "create"],
                         "the next hour may try again")

    def test_restart_agent_not_ready_banners_and_does_not_retry(self):
        self.watch("legwork")
        self.agents(orch("working"))
        self.fail("agent start", "agent_not_ready")
        self.run_pass()
        (action,) = [a for a in self.log()[-1]["actions"] if a["kind"] == "restart"]
        self.assertIs(action["ok"], False)
        self.assertIn("agent_not_ready", action["error"])
        self.assertEqual(self.prompts(), [])
        (msg,) = self.banners()
        self.assertIn("agent_not_ready", msg)
        self.run_pass(at=T0 + 30 * MIN)
        self.assertEqual((self.restart_calls(), self.banners()), ([], []))

    def test_first_restart_failure_is_logged_without_banner(self):
        self.watch("legwork")
        self.agents(orch("working"))
        self.fail("workspace create", "server_error")
        self.run_pass()
        (action,) = [a for a in self.log()[-1]["actions"] if a["kind"] == "restart"]
        self.assertIs(action["ok"], False)
        self.assertEqual(self.banners(), [])
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.restart_calls(), [])
        self.assertEqual(len(self.banners()), 1)

    def test_restart_banner_retried_when_notifier_fails(self):
        self.watch("legwork")
        self.agents(orch("working"))
        self.fail("agent start", "agent_not_ready")
        self.world.banner_code = 3
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(len(self.banners()), 1)
        self.assertEqual(self.restart_calls(), [])

    def test_restart_window_clears_when_switchboard_returns(self):
        self.watch("legwork")
        self.agents(orch("working"))
        self.fail("agent start", "agent_not_ready")
        self.run_pass()
        self.heal()
        self.agents(sw("working"), orch("working"))
        self.run_pass(at=T0 + 5 * MIN)
        self.agents(orch("working"))
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.restart_calls(), [], "still the same hour")
        self.assertEqual(len(self.banners()), 1, "a new outage, a new banner")


class SwitchOffTests(HealthCase):
    """Switching itself off when there is nothing to watch."""

    def test_watching_nothing_on_two_passes_switches_the_watcher_off(self):
        self.state([])
        self.agents(sw("idle"))
        self.run_pass()
        self.assertEqual(self.ctl_calls(), [])
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.ctl_calls(), [["off"]])
        self.assertIn({"kind": "self-off"}, self.log()[-1]["actions"])
        self.assertEqual(len(self.log()), 2, "the pass is logged before the watcher goes")

    def test_no_state_file_counts_as_watching_nothing(self):
        self.agents(orch("working"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.ctl_calls(), [["off"]])

    def test_switchboard_missing_and_watching_nothing_switches_off(self):
        self.state([])
        self.agents(orch("working"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.ctl_calls(), [["off"]])
        self.assertEqual(self.restart_calls(), [])

    def test_herdr_unreachable_and_watching_nothing_still_switches_off(self):
        self.state([])
        self.fail("agent list", "server_unavailable")
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.ctl_calls(), [["off"]])

    def test_a_watched_project_restarts_the_count(self):
        self.state([])
        self.agents(sw("working"), orch("working"))
        self.run_pass()
        self.watch("legwork")
        self.run_pass(at=T0 + 5 * MIN)
        self.state([])
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.ctl_calls(), [])
        self.run_pass(at=T0 + 15 * MIN)
        self.assertEqual(self.ctl_calls(), [["off"]])

    def test_never_switches_off_while_watching(self):
        self.watch("legwork")
        self.agents(sw("working"), orch("working"))
        for i in range(4):
            self.run_pass(at=T0 + i * 5 * MIN)
            self.assertEqual(self.ctl_calls(), [])

    def test_cycle_marker_is_not_watching_nothing(self):
        self.state([], cycle=True)
        self.agents(sw("working"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.ctl_calls(), [])

    def test_long_gap_restarts_the_off_count(self):
        self.state([])
        self.agents(sw("idle"))
        self.run_pass()
        self.run_pass(at=T0 + 60 * MIN)
        self.assertEqual(self.ctl_calls(), [])
        self.run_pass(at=T0 + 65 * MIN)
        self.assertEqual(self.ctl_calls(), [["off"]])

    def test_count_starts_again_after_switching_off(self):
        self.state([])
        self.agents(sw("idle"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.ctl_calls(), [["off"]])
        self.run_pass(at=T0 + 10 * MIN)
        self.assertEqual(self.ctl_calls(), [], "switched on again: one empty pass is not enough")

    def test_dry_run_never_switches_off(self):
        self.state([])
        self.agents(sw("idle"))
        self.run_pass()
        before = self.state_path.read_text(encoding="utf-8")
        self.run_pass("--dry-run", at=T0 + 5 * MIN)
        self.assertIn("would switch the health watcher off", self.out)
        self.assertEqual(self.ctl_calls(), [])
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)


class LoginTests(HealthCase):
    """Windows: the scheduled task carries the sign-in it was switched on in.
    A pass in a later sign-in switches the watcher off and checks nothing,
    as the Mac's launch agent is gone after every login."""

    def test_a_new_sign_in_switches_off_without_a_pass(self):
        self.patch("login_mark", lambda: "222")
        self.watch("legwork")
        self.agents(sw("idle"), orch("blocked"))
        self.run_pass("--login", "111")
        self.assertEqual(self.ctl_calls(), [["off"]])
        self.assertEqual(self.world.herdr_calls, [])
        (line,) = self.log()
        self.assertTrue(any("sign-in" in n for n in line["notes"]))

    def test_the_same_sign_in_runs_the_pass(self):
        self.patch("login_mark", lambda: "111")
        self.watch("legwork")
        self.agents(sw("working"), orch("working"))
        self.run_pass("--login", "111")
        self.assertEqual(self.ctl_calls(), [])
        self.assertTrue(self.world.herdr_calls)

    def test_an_unreadable_sign_in_runs_the_pass(self):
        self.watch("legwork")
        self.agents(sw("working"), orch("working"))
        self.run_pass("--login", "111")
        self.assertEqual(self.ctl_calls(), [])
        self.assertTrue(self.world.herdr_calls)

    def test_dry_run_only_says_it_would_switch_off(self):
        self.patch("login_mark", lambda: "222")
        self.watch("legwork")
        self.run_pass("--dry-run", "--login", "111")
        self.assertIn("would switch the health watcher off", self.out)
        self.assertEqual(self.ctl_calls(), [])
        self.assertFalse(self.log_path.exists())

    @unittest.skipUnless(os.name == "nt", "the sign-in time comes from the Windows session API")
    def test_login_mark_reads_this_sign_in(self):
        mod = load(SCRIPT, "switchboard_health_real")
        mark = mod.login_mark()
        self.assertTrue(mark and mark.isdigit(), mark)
        self.assertEqual(mod.login_mark(), mark)


class DryRunTests(HealthCase):

    def test_dry_run_prints_and_sends_nothing(self):
        self.state([("legwork", str(self.repo), "w1:p2")], cycle=True)
        self.agents(sw("idle"), orch("blocked"))
        self.assertEqual(self.run_pass("--dry-run"), 0)
        self.assertIn("would prompt wB:p1: /clear", self.out)
        self.assertEqual((self.prompts(), self.banners()), ([], []))
        self.assertFalse(self.log_path.exists())
        self.assertFalse(self.state_path.exists())

    def test_dry_run_restart_and_banner_send_nothing(self):
        self.watch("legwork")
        self.agents(orch("working"))
        self.run_pass("--dry-run")
        self.assertIn("would restart the switchboard", self.out)
        self.assertEqual((self.restart_calls(), self.banners()), ([], []))
        self.agents(sw("blocked"), orch("working"))
        self.run_pass("--dry-run")
        self.assertIn("would banner", self.out)
        self.assertEqual(self.banners(), [])

    def test_dry_run_leaves_two_pass_state_alone(self):
        self.watch("legwork")
        self.agents(sw("idle"), orch("working"))
        self.run_pass()
        before = self.state_path.read_text(encoding="utf-8")
        self.run_pass("--dry-run", at=T0 + 5 * MIN)
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)
        self.assertEqual(len(self.log()), 1)


class WindowlessTests(HealthCase):
    """The Windows task runs under pythonw.exe, which has no stdout or
    stderr. The pass writes them to DIR's log files, as launchd does."""

    def test_no_stderr_goes_to_the_error_log(self):
        self.watch("legwork")
        self.agents(sw("blocked"), orch("working"))
        self.world.banner_code = 1
        opened = []
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None), \
                mock.patch.object(self.mod, "now", lambda: T0):
            try:
                self.assertEqual(self.mod.main(["--once", "--state-dir", str(self.dir)]), 0)
            finally:
                opened = [f for f in (sys.stdout, sys.stderr) if f is not None]
                for f in opened:
                    f.close()
        self.assertEqual(len(opened), 2)
        self.assertIn("no banner", (self.dir / "health.err.log").read_text(encoding="utf-8"))
        self.assertTrue((self.dir / "health.out.log").exists())

    def test_a_console_keeps_its_own_streams(self):
        self.watch("legwork")
        self.agents(sw("working"), orch("working"))
        self.run_pass()
        self.assertFalse((self.dir / "health.err.log").exists())


class RobustnessTests(HealthCase):

    def test_last_restart_survives_a_crash(self):
        self.watch("legwork")
        self.agents(orch("working"))
        real = self.mod.restart

        def boom():
            raise RuntimeError("herdr went away mid-restart")

        with mock.patch.object(self.mod, "restart", boom):
            with self.assertRaises(RuntimeError):
                self.run_pass()
        self.assertIs(self.mod.restart, real)
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.restart_calls(), [], "the crash must not reset the hourly cap")
        self.assertIn("/switchboard resume", self.banners()[0])

    def test_bad_switchboard_pane_id_is_never_passed_to_herdr(self):
        for bad in (12, None, "-rf", "--help", ""):
            with self.subTest(bad=bad):
                if self.state_path.exists():
                    self.state_path.unlink()
                self.state([("legwork", str(self.repo), "w1:p2")], cycle=True)
                self.agents(sw("idle", pane=bad), orch("working"))
                self.assertEqual(self.run_pass(), 0)
                self.assertEqual(self.prompts(), [])
                self.assertTrue(any("pane id" in e for e in self.log()[-1]["errors"]))

    def test_bad_orchestrator_pane_id_skips_that_repo_only(self):
        self.state([("legwork", str(self.repo), "--help"), ("recipe-box", str(self.repo2), "w2:p2")])
        self.world.running = True
        self.agents(sw("idle"), orch("blocked", pane="w2:p2", name="recipe-box"))
        self.run_pass()
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.prompt_texts(),
                         ["SWITCHBOARD HEALTH check-in recipe-box: pane blocked"])
        self.assertFalse(any("--help" in c for c in self.world.herdr_calls))
        self.assertTrue(any("pane id" in e for e in self.log()[-1]["errors"]))

    def test_bad_root_pane_from_workspace_create_stops_the_restart(self):
        self.watch("legwork")
        self.agents(orch("working"))
        self.world.root_pane = ["wS:p1"]
        self.run_pass()
        (action,) = [a for a in self.log()[-1]["actions"] if a["kind"] == "restart"]
        self.assertIs(action["ok"], False)
        self.assertFalse(any(c[:2] == ["agent", "start"] for c in self.world.herdr_calls))

    def test_one_failing_repo_check_does_not_stop_the_pass(self):
        self.watch("legwork", "recipe-box")
        self.agents(sw("idle"), orch("working"),
                    orch("blocked", pane="w2:p2", name="recipe-box"))

        def flaky(repo, pane):
            if repo == str(self.repo):
                raise ValueError("unreadable board")
            return ["pane blocked", "wait running since 18:00 (pid 4242)"], None

        self.patch("wait_status", flaky)
        self.assertEqual(self.run_pass(), 0)
        self.run_pass(at=T0 + 5 * MIN)
        self.assertEqual(self.prompt_texts(),
                         ["SWITCHBOARD HEALTH check-in recipe-box: pane blocked"])
        (err,) = self.log()[-1]["errors"]
        self.assertIn("unreadable board", err)
        self.assertIn(str(self.repo), err)


if __name__ == "__main__":
    unittest.main()
