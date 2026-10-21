"""Tests for orchestration/bin/pane-notify.

The script runs in process. Its herdr() is a fake that records each call and
answers `pane get` with the pane the test sets. Its run() (the banner) and
spawn() (the sound) are fakes that record the command, stdin and environment,
so a banner is a run() call and a sound a spawn() call. WINDOWS picks the
form, so both the Mac form (terminal-notifier and afplay) and the Windows
form (a toast through powershell.exe and a built-in Windows sound) run on
every platform. Herdr's sounds and the Windows sounds are files in a
temporary folder. The hook input is the JSON Claude Code sends on stdin.
Pane shapes follow herdr 0.9.0's `pane get` (seen 2026-10-21): label and
title are left out when unset, terminal_title_stripped is the window title.
"""
import base64
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "orchestration" / "bin" / "pane-notify"
PANE_ID = "w1:p2"


def load_script():
    loader = importlib.machinery.SourceFileLoader("pane_notify", str(SCRIPT))
    spec = importlib.util.spec_from_loader("pane_notify", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def stop(**extra):
    return dict({"session_id": "s1", "hook_event_name": "Stop",
                 "cwd": "/srv/legwork", "stop_hook_active": False}, **extra)


def notification(kind="permission_prompt", message="Claude needs your permission to use Bash"):
    return {"session_id": "s1", "hook_event_name": "Notification",
            "cwd": "/srv/legwork", "message": message, "notification_type": kind}


def opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


class NotifyCase(unittest.TestCase):
    windows = False

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.script = load_script()
        self.herdr_calls, self.runs, self.spawns = [], [], []
        self.pane_json = {"cwd": "/srv/legwork"}
        self.get_fails = False
        self.run_raises = None

        def herdr(*args):
            self.herdr_calls.append(list(args))
            if args[:2] == ("pane", "get") and not self.get_fails:
                pane = dict(self.pane_json, pane_id=args[2])
                return json.dumps({"id": "cli:pane:get",
                                   "result": {"pane": pane, "type": "pane_info"}})
            return None

        def run(argv, **kw):
            self.runs.append((list(argv), kw))
            if self.run_raises:
                raise self.run_raises
            return subprocess.CompletedProcess(argv, 0, "", "")

        def spawn(argv, **kw):
            self.spawns.append((list(argv), kw))

        self.herdr_sounds = self.tmp / "herdr-sounds"
        self.herdr_sounds.mkdir()
        self.win_media = self.tmp / "Media"
        self.win_media.mkdir()
        for name in ("done.mp3", "request.mp3"):
            (self.herdr_sounds / name).write_bytes(b"ID3")
        win_sounds = {"done": self.win_media / "done.wav",
                      "request": self.win_media / "request.wav"}
        for path in win_sounds.values():
            path.write_bytes(b"RIFF")
        self.win_sounds = win_sounds

        for name, value in (("herdr", herdr), ("run", run), ("spawn", spawn),
                            ("WINDOWS", self.windows),
                            ("HERDR_SOUNDS", self.herdr_sounds),
                            ("WINDOWS_SOUNDS", win_sounds)):
            patch = mock.patch.object(self.script, name, value)
            patch.start()
            self.addCleanup(patch.stop)

        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in list(os.environ):
            if key.startswith(("HERDR_", "CLAUDE_CODE_ENTRYPOINT", "PANE_NOTIFY")):
                del os.environ[key]

    def pane(self, **pane):
        pane.setdefault("cwd", "/srv/legwork")
        self.pane_json = pane

    def fire(self, hook, herdr=True, raw=None, **env):
        if herdr:
            os.environ.update(HERDR_ENV="1", HERDR_PANE_ID=PANE_ID)
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        os.environ.update(env)
        text = raw if raw is not None else json.dumps(hook)
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(text)), \
                contextlib.redirect_stdout(out):
            self.script.main()
        self.assertEqual(out.getvalue(), "")

    # The banner and the sound, whichever form is in use.

    def shows(self):
        """[(title, body)] for every banner."""
        out = []
        for argv, kw in self.runs:
            if self.windows:
                out.append((kw["env"]["PANE_NOTIFY_TITLE"], kw["env"]["PANE_NOTIFY_BODY"]))
            else:
                out.append((opt(argv, "-title"), kw.get("input")))
        return out

    def played(self):
        """[path] for every sound."""
        return [argv[-1] for argv, kw in self.spawns]

    def sound(self, name):
        if self.windows:
            return str(self.win_sounds[name])
        return str(self.herdr_sounds / f"{name}.mp3")


class Rules:
    """Who notifies, what it says and never being in the way: both forms."""

    # Who notifies

    def test_orchestrator_pane_by_title_notifies_done(self):
        self.pane(title="orch-legwork")
        self.fire(stop())
        self.assertEqual(self.shows(), [("orch-legwork: done", "Claude finished")])
        self.assertEqual(self.played(), [self.sound("done")])
        self.assertIn(["pane", "get", PANE_ID], self.herdr_calls)

    def test_orchestrator_pane_by_label_notifies(self):
        self.pane(label="orch-recipe-box", title="Fix the stage order")
        self.fire(stop())
        [(title, _)] = self.shows()
        self.assertEqual(title, "orch-recipe-box: done")

    def test_normal_session_notifies_under_its_title(self):
        self.pane(title="Overnight run")
        self.fire(stop())
        [(title, _)] = self.shows()
        self.assertEqual(title, "Overnight run: done")

    def test_normal_session_uses_the_window_title_when_herdr_has_no_title(self):
        # herdr 0.9.0 leaves title out for a plain Claude pane.
        self.pane(terminal_title="\u2733 Budget spike", terminal_title_stripped="Budget spike")
        self.fire(stop())
        [(title, _)] = self.shows()
        self.assertEqual(title, "Budget spike: done")

    def test_normal_session_without_any_title_uses_directory_name(self):
        self.pane(cwd="/srv/recipe-box/")
        self.fire(stop())
        [(title, _)] = self.shows()
        self.assertEqual(title, "recipe-box: done")

    def test_windows_directory_name(self):
        self.pane(cwd="E:\\dev\\projects\\recipe-box")
        self.fire(stop())
        [(title, _)] = self.shows()
        self.assertEqual(title, "recipe-box: done")

    def test_switchboard_pane_is_quiet(self):
        for pane in ({"label": "switchboard", "title": "switchboard"},
                     {"label": "switchboard", "title": "orch-legwork"},
                     {"label": "switchboard"},
                     {"title": "switchboard"},
                     {"terminal_title_stripped": "switchboard"}):
            with self.subTest(pane=pane):
                self.runs.clear()
                self.pane(**pane)
                self.fire(stop())
                self.fire(notification())
                self.assertEqual(self.shows(), [])
                self.assertEqual(self.spawns, [])

    def test_fanout_worker_pane_is_quiet(self):
        for pane in ({"label": "fw-wait-script", "title": "Rewrite the wait script"},
                     {"label": "fw-x"}):
            with self.subTest(pane=pane):
                self.pane(**pane)
                self.fire(stop())
                self.fire(notification())
                self.assertEqual(self.shows(), [])
                self.assertEqual(self.spawns, [])

    def test_label_beats_title(self):
        # A worker whose session got renamed still counts as a worker.
        self.pane(label="fw-x", title="orch-legwork")
        self.fire(stop())
        self.assertEqual(self.shows(), [])

    def test_subagent_is_quiet_and_asks_herdr_nothing(self):
        self.pane(title="orch-legwork")
        self.fire(stop(agent_id="a1", agent_type="general-purpose"))
        self.fire(dict(notification(), agent_id="a1"))
        self.assertEqual(self.herdr_calls, [])
        self.assertEqual(self.runs + self.spawns, [])

    def test_subagent_stop_is_quiet(self):
        self.pane(title="orch-legwork")
        self.fire({"session_id": "s1", "hook_event_name": "SubagentStop"})
        self.assertEqual(self.herdr_calls, [])
        self.assertEqual(self.runs + self.spawns, [])

    def test_headless_claude_is_quiet(self):
        # claude -p run from a script in an orchestrator's pane inherits its pane id.
        self.pane(title="orch-legwork")
        for entrypoint in ("sdk-cli", "sdk-py", "sdk-ts"):
            with self.subTest(entrypoint=entrypoint):
                self.fire(stop(), CLAUDE_CODE_ENTRYPOINT=entrypoint)
        self.assertEqual(self.herdr_calls, [])
        self.assertEqual(self.runs + self.spawns, [])

    def test_outside_herdr_does_nothing(self):
        self.fire(stop(), herdr=False)
        self.assertEqual(self.herdr_calls, [])
        self.assertEqual(self.runs + self.spawns, [])

    def test_herdr_env_without_a_pane_id_does_nothing(self):
        os.environ["HERDR_ENV"] = "1"
        self.fire(stop(), herdr=False)
        self.assertEqual(self.herdr_calls, [])

    # What it says

    def test_permission_prompt_asks_with_request_sound(self):
        self.pane(title="orch-legwork")
        self.fire(notification())
        self.assertEqual(self.shows(), [("orch-legwork: needs you",
                                         "Claude needs your permission to use Bash")])
        self.assertEqual(self.played(), [self.sound("request")])

    def test_elicitation_dialog_asks(self):
        self.pane(title="orch-legwork")
        self.fire(notification("elicitation_dialog", "Claude has a question"))
        self.assertEqual(self.shows(), [("orch-legwork: needs you", "Claude has a question")])
        self.assertEqual(self.played(), [self.sound("request")])

    def test_other_notifications_are_quiet(self):
        # idle_prompt repeats a done.
        self.pane(title="orch-legwork")
        for kind in ("idle_prompt", "auth_success", ""):
            with self.subTest(kind=kind):
                self.fire(notification(kind, "Claude is waiting for your input"))
        self.assertEqual(self.runs + self.spawns, [])

    def test_done_body_is_first_line_of_last_message(self):
        self.pane(title="orch-legwork")
        self.fire(stop(last_assistant_message="\nPR 29 is open with the hook.\n\nDetails."))
        self.assertEqual(self.shows(), [("orch-legwork: done", "PR 29 is open with the hook.")])

    def test_long_body_is_cut(self):
        self.pane(title="orch-legwork")
        self.fire(stop(last_assistant_message="x" * 500))
        [(_, body)] = self.shows()
        self.assertLessEqual(len(body), 120)
        self.assertTrue(body.endswith("..."))

    def test_title_is_cut(self):
        self.pane(title="t" * 300)
        self.fire(stop())
        [(title, _)] = self.shows()
        self.assertLessEqual(len(title), 80)
        self.assertTrue(title.endswith("...: done"))

    def test_body_keeps_a_leading_dash_bracket_or_quote(self):
        self.pane(title="orch-legwork")
        for first in ("- PR 30 is open.", "[x] done", "(see below)", '"quoted"', "<b>&amp;"):
            with self.subTest(first=first):
                self.runs.clear()
                self.fire(stop(last_assistant_message=first + "\nmore"))
                self.assertEqual(self.shows(), [("orch-legwork: done", first)])

    # Never in the way

    def test_unknown_pane_is_quiet(self):
        # Can't tell a worker from an orchestrator: say nothing.
        self.get_fails = True
        self.fire(stop())
        self.assertEqual(self.runs + self.spawns, [])

    def test_herdr_answer_that_is_not_a_pane_is_quiet(self):
        self.script.herdr = lambda *args: "not json"
        self.fire(stop())
        self.assertEqual(self.runs + self.spawns, [])

    def test_bad_input_is_quiet(self):
        self.pane(title="orch-legwork")
        for raw in ("not json", "[1, 2]", ""):
            with self.subTest(raw=raw):
                self.fire(None, raw=raw)
        self.assertEqual(self.runs + self.spawns, [])

    def test_failed_banner_still_plays_the_sound(self):
        self.pane(title="orch-legwork")
        self.run_raises = OSError("no such file")
        self.fire(stop())
        self.assertEqual(self.played(), [self.sound("done")])

    def test_timed_out_banner_still_plays_the_sound(self):
        self.pane(title="orch-legwork")
        self.run_raises = subprocess.TimeoutExpired("notifier", 5)
        self.fire(stop())
        self.assertEqual(self.played(), [self.sound("done")])

    def test_missing_sound_file_still_shows_banner(self):
        Path(self.sound("done")).unlink()
        self.pane(title="orch-legwork")
        self.fire(stop())
        self.assertEqual(len(self.shows()), 1)
        self.assertEqual(self.spawns, [])

    def test_sound_is_detached_and_silent(self):
        self.pane(title="orch-legwork")
        self.fire(stop())
        [(_, kw)] = self.spawns
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(kw.get(stream), subprocess.DEVNULL, stream)


class MacFormTests(Rules, NotifyCase):
    windows = False

    def test_banner_is_terminal_notifier_with_the_body_on_stdin(self):
        # As -message, a body starting with -, [, (, { or a quote is refused.
        self.pane(title="orch-legwork")
        with mock.patch.object(self.script.shutil, "which",
                               lambda name: "/usr/local/bin/" + name):
            self.fire(stop(last_assistant_message="- a bullet"))
        [(argv, kw)] = self.runs
        self.assertEqual(argv, ["/usr/local/bin/terminal-notifier", "-title", "orch-legwork: done"])
        self.assertEqual(kw["input"], "- a bullet")
        self.assertNotIn("-message", argv)

    def test_banner_carries_no_sound_of_its_own(self):
        # The sound is Herdr's chime through afplay; a -sound would play twice.
        self.pane(title="orch-legwork")
        self.fire(stop())
        [(argv, _)] = self.runs
        self.assertNotIn("-sound", argv)

    def test_notifier_falls_back_to_the_homebrew_path(self):
        # A hook's PATH may lack Homebrew.
        self.pane(title="orch-legwork")
        with mock.patch.object(self.script.shutil, "which", lambda name: None):
            self.fire(stop())
        [(argv, _)] = self.runs
        self.assertEqual(argv[0], "/opt/homebrew/bin/terminal-notifier")

    def test_sound_is_herdrs_chime_through_afplay(self):
        self.pane(title="orch-legwork")
        self.fire(stop())
        [(argv, kw)] = self.spawns
        self.assertEqual(argv, ["afplay", str(self.herdr_sounds / "done.mp3")])
        self.assertTrue(kw.get("start_new_session"))


class WindowsFormTests(Rules, NotifyCase):
    windows = True

    def toast_script(self, argv):
        return base64.b64decode(opt(argv, "-EncodedCommand")).decode("utf-16-le")

    def test_banner_is_a_toast_through_windows_powershell(self):
        self.pane(title="orch-legwork")
        with mock.patch.object(self.script.shutil, "which",
                               lambda name: "D:\\PS\\" + name + ".exe"):
            self.fire(stop(last_assistant_message="PR 7 is open"))
        [(argv, kw)] = self.runs
        self.assertEqual(argv[0], "D:\\PS\\powershell.exe")
        self.assertIn("-NoProfile", argv)
        self.assertIn("-NonInteractive", argv)
        script = self.toast_script(argv)
        self.assertIn("ToastNotificationManager", script)
        self.assertIn("$env:PANE_NOTIFY_TITLE", script)
        self.assertIn("$env:PANE_NOTIFY_BODY", script)
        self.assertEqual(kw["env"]["PANE_NOTIFY_TITLE"], "orch-legwork: done")
        self.assertEqual(kw["env"]["PANE_NOTIFY_BODY"], "PR 7 is open")
        self.assertEqual(kw.get("creationflags"), self.script.CREATE_NO_WINDOW)

    def test_title_and_body_never_reach_the_command_line(self):
        # They go through the environment, so no quoting can break the toast.
        self.pane(title="orch-$(x) 'a' \"b\"")
        self.fire(stop(last_assistant_message="`whoami`; $env:PATH & <x>"))
        [(argv, kw)] = self.runs
        joined = " ".join(argv) + self.toast_script(argv)
        self.assertNotIn("whoami", joined)
        self.assertNotIn("orch-$(x)", joined)
        self.assertEqual(kw["env"]["PANE_NOTIFY_BODY"], "`whoami`; $env:PATH & <x>")

    def test_toast_is_silent_because_the_sound_plays_on_its_own(self):
        self.pane(title="orch-legwork")
        self.fire(stop())
        [(argv, _)] = self.runs
        self.assertIn('silent="true"', self.toast_script(argv))

    def test_toast_needs_no_extra_modules(self):
        self.pane(title="orch-legwork")
        self.fire(stop())
        [(argv, _)] = self.runs
        script = self.toast_script(argv)
        self.assertNotIn("Import-Module", script)
        self.assertNotIn("BurntToast", script)

    def test_powershell_falls_back_to_the_system_folder(self):
        self.pane(title="orch-legwork")
        os.environ["SystemRoot"] = "D:\\Win"
        with mock.patch.object(self.script.shutil, "which", lambda name: None):
            self.fire(stop())
        [(argv, _)] = self.runs
        self.assertEqual(argv[0], os.path.join("D:\\Win", "System32", "WindowsPowerShell",
                                               "v1.0", "powershell.exe"))

    def test_sound_is_a_windows_sound_through_python(self):
        self.pane(title="orch-legwork")
        self.fire(notification())
        [(argv, kw)] = self.spawns
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1:3], ["-I", "-c"])  # isolated: no winsound.py from the cwd
        self.assertIn("winsound", argv[3])
        self.assertEqual(argv[-1], str(self.win_sounds["request"]))
        self.assertEqual(kw.get("creationflags"), self.script.DETACHED)

    def test_default_sounds_are_built_in_windows_wavs(self):
        fresh = load_script()
        for name in ("done", "request"):
            path = str(fresh.WINDOWS_SOUNDS[name])
            self.assertTrue(path.endswith(".wav"), path)
            self.assertIn("Media", path)


class RealProcessTests(unittest.TestCase):
    """The real herdr(), run() and the script as a process."""

    def setUp(self):
        self.script = load_script()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("HERDR_BIN_PATH", None)

    def test_missing_herdr_answers_none(self):
        os.environ["PATH"] = ""
        self.assertIsNone(self.script.herdr("pane", "get", PANE_ID))

    def test_herdr_comes_from_herdr_bin_path_when_set(self):
        # Herdr sets HERDR_BIN_PATH in every pane.
        seen = []

        def fake_run(argv, **kw):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "{}", "")

        os.environ["HERDR_BIN_PATH"] = "/opt/herdr/herdr"
        with mock.patch.object(self.script.subprocess, "run", fake_run):
            self.assertEqual(self.script.herdr("pane", "get", PANE_ID), "{}")
        self.assertEqual(seen, [["/opt/herdr/herdr", "pane", "get", PANE_ID]])

    def test_herdr_failure_answers_none(self):
        def fake_run(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", "pane not found")

        with mock.patch.object(self.script.subprocess, "run", fake_run):
            self.assertIsNone(self.script.herdr("pane", "get", PANE_ID))

    def test_herdr_timeout_answers_none(self):
        def fake_run(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 5)

        with mock.patch.object(self.script.subprocess, "run", fake_run):
            self.assertIsNone(self.script.herdr("pane", "get", PANE_ID))

    def test_missing_program_is_swallowed(self):
        self.assertIsNone(self.script.run([str(Path(tempfile.gettempdir()) / "no-such-notifier")],
                                          input="x"))
        self.script.spawn([str(Path(tempfile.gettempdir()) / "no-such-player")],
                          stdin=subprocess.DEVNULL)

    def test_script_always_exits_zero_and_prints_nothing(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
        for text in ("not json", json.dumps(stop())):
            with self.subTest(text=text):
                done = subprocess.run([sys.executable, str(SCRIPT)], input=text,
                                      capture_output=True, text=True, env=env, timeout=60)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stdout, "")
                self.assertEqual(done.stderr, "")

    def test_script_is_executable_in_git(self):
        out = subprocess.run(["git", "ls-files", "-s", str(SCRIPT)], cwd=str(HERE.parent),
                             capture_output=True, text=True)
        if out.returncode != 0 or not out.stdout:
            self.skipTest("not a git checkout")
        self.assertTrue(out.stdout.startswith("100755"), out.stdout)


@unittest.skipUnless(os.name == "nt", "Windows PowerShell")
class WindowsLiveTests(unittest.TestCase):
    """On Windows: the toast script parses and the built-in sounds exist.
    Nothing is shown and nothing plays."""

    def test_toast_script_parses_in_windows_powershell(self):
        script = load_script()
        ps = script.powershell()
        if not os.path.isfile(ps) and not shutil.which(ps):
            self.skipTest("no powershell.exe")
        check = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseInput("
                 "$env:PANE_NOTIFY_CHECK, [ref]$null, [ref]$e); if ($e) { $e; exit 1 }")
        done = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", check],
                              capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, PANE_NOTIFY_CHECK=script.TOAST))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_built_in_sounds_exist(self):
        script = load_script()
        for name, path in script.WINDOWS_SOUNDS.items():
            self.assertTrue(Path(path).is_file(), f"{name}: {path}")


if __name__ == "__main__":
    unittest.main()
