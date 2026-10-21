"""Tests for the optional orchestration layer under orchestration/.

Covers the installer's pure functions, the settings the scripts read
(LEGWORK_BUGBOT_OWNERS, LEGWORK_USAGE_CACHE), the wizard carrying those
settings through a config rewrite, real runs into scratch dirs, and scans
that keep the public copies free of anything tied to one person's setup and
of the switchboard legwork no longer ships.
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
ORCH = REPO / "orchestration"
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "core"))

import legwork_install  # noqa: E402


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


install = load(ORCH / "install.py", "orchestration_install")
from legwork_common import switchboard_mode  # noqa: E402

# The dropped switchboard's name, read from the one compatibility line in
# orchestration/install.py, so no test spells it out.
OLD = install.RETIRED_KEYS[0].split("_")[1].lower()


def no_tool(_name):
    return None


def every_tool(name):
    return f"/fake/bin/{name}"


class PiecesTests(unittest.TestCase):
    def test_parse_pieces_keeps_canonical_order(self):
        self.assertEqual(install.parse_pieces("pane-notify, shipping"),
                         ["shipping", "pane-notify"])
        self.assertEqual(install.parse_pieces("all"), list(install.PIECES))
        self.assertEqual(install.parse_pieces(""), [])

    def test_parse_pieces_rejects_unknown(self):
        with self.assertRaises(ValueError):
            install.parse_pieces("shipping,runner")

    def test_the_old_switchboard_piece_installs_nothing(self):
        self.assertEqual(install.parse_pieces(OLD), [])
        self.assertEqual(install.parse_pieces(f"shipping,{OLD}"), ["shipping"])
        self.assertNotIn(OLD, install.PIECES)
        self.assertNotIn(OLD, install.parse_pieces("all"))
        self.assertFalse(hasattr(install, OLD + "_home"))

    def test_new_pieces_are_in_all(self):
        for piece in ("switchboard-health", "pane-notify"):
            self.assertIn(piece, install.parse_pieces("all"))

    def test_docstring_names_every_piece(self):
        for piece in install.PIECES:
            self.assertRegex(install.__doc__, rf"\n  {re.escape(piece)} ", piece)

    def test_plan_copies_mirrors_layout_and_sources_exist(self):
        dest = Path("/tmp/fake-claude")
        pairs = install.plan_copies(["shipping", "fanout", "switchboard"], dest)
        got = {d.relative_to(dest).as_posix() for _, d in pairs}
        self.assertEqual(got, {
            "commands/ship.md", "commands/bugbot.md", "commands/fanout.md",
            "skills/fanout/SKILL.md", "skills/switchboard-protocol/SKILL.md",
            "skills/switchboard/SKILL.md", "skills/roadmap-planning/SKILL.md"})
        for src, _ in pairs:
            self.assertTrue(src.is_file(), src)

    def test_manual_switchboard_leaves_out_the_session_skill_only(self):
        # Orchestrators follow the protocol either way, and a planning
        # session works with the user relaying by hand too. Only the
        # switchboard session's own skill is for a Claude Code switchboard.
        dest = Path("/tmp/fake-claude")
        pairs = install.plan_copies(["switchboard"], dest, switchboard="manual")
        self.assertEqual([d.relative_to(dest).as_posix() for _, d in pairs],
                         ["skills/switchboard-protocol/SKILL.md",
                          "skills/roadmap-planning/SKILL.md"])
        pairs = install.plan_copies(["switchboard"], dest, switchboard="claude")
        self.assertEqual(len(pairs), 3)

    def test_switchboard_value_is_claude_unless_manual(self):
        for raw, want in ((None, "claude"), ("", "claude"), ("claude", "claude"),
                          ("manual", "manual"), (" Manual ", "manual"),
                          (OLD, "claude")):
            self.assertEqual(switchboard_mode(raw), want, raw)

    def test_pane_config_dir_follows_a_non_default_claude_dir(self):
        home = Path("/home/u")
        self.assertIsNone(install.pane_config_dir(None, home / ".claude", home))
        self.assertEqual(install.pane_config_dir(None, Path("/alt"), home),
                         str(Path("/alt")))
        self.assertEqual(install.pane_config_dir("/x", Path("/alt"), home), "/x")
        self.assertIsNone(install.pane_config_dir("", Path("/alt"), home))

    # fanout and the protocol send cross-model reviews to "the codex agent,
    # when you have one"; this piece is how you get one.
    def test_codex_agent_installs_the_agent_definition(self):
        dest = Path("/tmp/fake-claude")
        pairs = install.plan_copies(["codex-agent"], dest)
        self.assertEqual([d.relative_to(dest).as_posix() for _, d in pairs],
                         ["agents/codex.md"])
        text = pairs[0][0].read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: codex\n"))
        self.assertNotIn("{{", text)
        self.assertIn("codex-agent", install.parse_pieces("all"))

    def test_claude_home_prefers_explicit_then_env(self):
        self.assertEqual(install.claude_home("/x", {"CLAUDE_CONFIG_DIR": "/y"}),
                         Path("/x"))
        self.assertEqual(install.claude_home(None, {"CLAUDE_CONFIG_DIR": "/y"}),
                         Path("/y"))
        self.assertEqual(install.claude_home(None, {}), Path.home() / ".claude")


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.text = (ORCH / "skills" / "switchboard" / "SKILL.md").read_text(
            encoding="utf-8")

    def test_render_fills_every_placeholder(self):
        out = install.render(self.text, "/srv/legwork", "/srv/legwork/orchestration/bin", "/c/dir")
        self.assertNotIn("{{", out)
        self.assertIn("/srv/legwork/orchestration/bin/switchboard-wait", out)
        self.assertIn("--cwd <repo> --env\n   CLAUDE_CONFIG_DIR=/c/dir "
                      "--env DISABLE_AUTO_UPDATE=true", out)

    def test_render_without_config_dir_has_no_env_flag(self):
        out = install.render(self.text, "/srv/legwork")
        self.assertNotIn("CLAUDE_CONFIG_DIR=", out)
        self.assertIn("--cwd <repo> --env DISABLE_AUTO_UPDATE=true", out)

    def test_pane_config_dir_renders_with_forward_slashes(self):
        # The pane line runs through Git Bash too, which eats backslashes.
        with mock.patch.object(install, "Path", PureWindowsPath), \
                mock.patch.object(install, "script_python", return_value=None):
            out = install.render("x{{PANE_ENV}}", r"E:\q", r"E:\b",
                                 pane_config_dir=r"E:\dev\claude-alt")
        self.assertEqual(out, "x --env\n   CLAUDE_CONFIG_DIR=E:/dev/claude-alt")

    def test_every_installed_file_renders_clean(self):
        for src, _ in install.plan_copies(list(install.PIECES), Path("/c")):
            out = install.render(src.read_text(encoding="utf-8"), "/q", "/engine/bin")
            self.assertNotIn("{{", out, src)
            self.assertNotIn("<legwork>/orchestration", out, src)

    def test_scripts_path_defaults_to_this_checkout(self):
        with mock.patch.object(install, "script_python", return_value=None):
            out = install.render("{{LEGWORK_BIN}}/x", "/q")
        self.assertEqual(out, f"{(ORCH / 'bin').as_posix()}/x")

    def test_windows_paths_render_with_forward_slashes(self):
        # Claude runs these lines through Git Bash, which eats unquoted
        # backslashes: E:\dev\x/bugbot-wait became E:devx/bugbot-wait.
        with mock.patch.object(install, "Path", PureWindowsPath), \
                mock.patch.object(install, "script_python", return_value=None):
            out = install.render("{{LEGWORK_BIN}}/x {{LEGWORK_DIR}}",
                                 r"E:\dev\queue", r"E:\dev\engine\bin")
        self.assertEqual(out, "E:/dev/engine/bin/x E:/dev/queue")

    def test_posix_script_lines_run_by_shebang(self):
        with mock.patch.object(install, "script_python", return_value=None):
            out = install.render("`{{LEGWORK_BIN}}/bugbot-wait <pr> --trigger`",
                                 "/q", "/engine/bin")
        self.assertEqual(out, "`/engine/bin/bugbot-wait <pr> --trigger`")

    def test_windows_script_lines_name_the_interpreter(self):
        # The shebangs say python3, which on stock Windows is the Store stub,
        # so a bare script path fails there. The line spells out the
        # interpreter, slashed and quoted for the Git Bash that runs it.
        with mock.patch.object(install, "Path", PureWindowsPath):
            out = install.render(
                "`{{LEGWORK_BIN}}/bugbot-wait <pr> --trigger` and "
                "`{{LEGWORK_BIN}}/switchboard-wait --status r:none`",
                r"E:\q", r"E:\dev\engine\bin",
                python=r"C:\Program Files\Python312\python.exe")
        self.assertEqual(out, (
            "`'C:/Program Files/Python312/python.exe' "
            "E:/dev/engine/bin/bugbot-wait <pr> --trigger` and "
            "`'C:/Program Files/Python312/python.exe' "
            "E:/dev/engine/bin/switchboard-wait --status r:none`"))
        argv = shlex.split(out.split("`")[1])
        self.assertEqual(argv[:2], ["C:/Program Files/Python312/python.exe",
                                    "E:/dev/engine/bin/bugbot-wait"])

    def test_script_python_only_on_windows(self):
        with mock.patch.object(install, "os", SimpleNamespace(name="posix")):
            self.assertIsNone(install.script_python())
        with mock.patch.object(install, "os", SimpleNamespace(name="nt")), \
                mock.patch.object(install.sys, "executable", r"C:\Py\python.exe"):
            self.assertEqual(install.script_python(), r"C:\Py\python.exe")

    def test_every_script_line_gets_the_interpreter(self):
        sources = [s for s, _ in install.plan_copies(list(install.PIECES), Path("/c"))]
        self.assertIn(ORCH / "skills" / "switchboard" / "SKILL.md", sources)
        for src in sources:
            text = src.read_text(encoding="utf-8")
            calls = len(re.findall(r"\{\{LEGWORK_BIN\}\}/", text))
            out = install.render(text, "/q", "/engine/bin", python="/py/python")
            self.assertEqual(out.count("/py/python /engine/bin/"), calls, src)

    def test_rendered_line_runs_without_the_shebang(self):
        # The line, split the way bash splits it, starts the script under the
        # named interpreter: no python3 on PATH needed.
        out = install.render("{{LEGWORK_BIN}}/bugbot-wait --help", "/q",
                             python=sys.executable)
        run = subprocess.run(shlex.split(out), capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--trigger", run.stdout)

    def test_unknown_placeholder_fails(self):
        with self.assertRaises(ValueError):
            install.render("{{LEGWORK_DIR}} {{OTHER}}", "/x")


class ConfigTests(unittest.TestCase):
    def test_set_replaces_active_key_in_place(self):
        text = "# head\nLEGWORK_DIR=/a\nLEGWORK_BUGBOT_OWNERS=old\n# tail\n"
        out = install.set_config_keys(text, [("LEGWORK_BUGBOT_OWNERS", "new")])
        self.assertEqual(out, "# head\nLEGWORK_DIR=/a\nLEGWORK_BUGBOT_OWNERS=new\n# tail\n")

    def test_set_drops_duplicate_active_lines(self):
        out = install.set_config_keys("A=1\nA=2\n", [("A", "3")])
        self.assertEqual(out, "A=3\n")

    def test_set_appends_new_keys_under_one_section(self):
        out = install.set_config_keys("LEGWORK_DIR=/a\n", [("B", "1")])
        out = install.set_config_keys(out, [("C", "2")])
        self.assertEqual(out.count(install.CONFIG_SECTION), 1)
        tail = out.split(install.CONFIG_SECTION)[1]
        self.assertLess(tail.index("B=1"), tail.index("C=2"))

    def test_commented_example_is_not_treated_as_set(self):
        out = install.set_config_keys("# LEGWORK_OWN_OWNERS=you\n",
                                      [("LEGWORK_OWN_OWNERS", "me")])
        self.assertIn("# LEGWORK_OWN_OWNERS=you\n", out)
        self.assertIn("\nLEGWORK_OWN_OWNERS=me\n", out)

    def test_parse_set(self):
        self.assertEqual(install.parse_set(["A_B=x y", "C="]),
                         [("A_B", "x y"), ("C", "")])
        for bad in ("lower=x", "NOEQUALS", "=x"):
            with self.assertRaises(ValueError):
                install.parse_set([bad])

    def test_wizard_and_installer_share_the_section_and_keys(self):
        self.assertEqual(install.CONFIG_SECTION,
                         legwork_install.ORCHESTRATION_SECTION)
        documented = set(re.findall(r"^# (LEGWORK_[A-Z_]+)=",
                                    (REPO / "config.example").read_text(), re.M))
        self.assertLessEqual(set(legwork_install.ORCHESTRATION_KEYS), documented)

    def test_wizard_rewrite_keeps_orchestration_settings(self):
        existing = {"LEGWORK_DIR": "/a", "LEGWORK_OWN_OWNERS": "me",
                    "LEGWORK_LINEAR_TEAM": "Platform Team"}
        for level in (1, 2):
            v = {"level": level, "legwork_dir": "/a", "daily_cap": 8,
                 "carried": legwork_install.carried_settings(existing)}
            text = legwork_install.render_config(v)
            got = legwork_install.parse_config_text(text)
            self.assertEqual(got["LEGWORK_OWN_OWNERS"], "me", level)
            self.assertEqual(got["LEGWORK_LINEAR_TEAM"], "Platform Team", level)
            # and the installer still finds its section to append to
            again = install.set_config_keys(text, [("LEGWORK_SWITCHBOARD", "manual")])
            self.assertEqual(again.count(install.CONFIG_SECTION), 1)

    def test_wizard_carries_the_new_settings_and_not_the_retired_key(self):
        keys = legwork_install.ORCHESTRATION_KEYS
        for key in ("LEGWORK_SESSION_ARGS", "LEGWORK_SWITCHBOARD_HEALTH"):
            self.assertIn(key, keys)
        for key in install.RETIRED_KEYS:
            self.assertNotIn(key, keys)

    def test_wizard_rewrite_of_an_old_config_reads_as_claude(self):
        existing = {"LEGWORK_SWITCHBOARD": OLD, install.RETIRED_KEYS[0]: "work",
                    "LEGWORK_SESSION_ARGS": "--model opus --effort high",
                    "LEGWORK_SWITCHBOARD_HEALTH": "1"}
        carried = legwork_install.carried_settings(existing)
        self.assertEqual(carried, [
            ("LEGWORK_SWITCHBOARD", "claude"),
            ("LEGWORK_SWITCHBOARD_HEALTH", "1"),
            ("LEGWORK_SESSION_ARGS", "--model opus --effort high")])
        text = legwork_install.render_config(
            {"level": 1, "legwork_dir": "/a", "carried": carried})
        self.assertNotIn(install.RETIRED_KEYS[0], text)
        self.assertEqual(legwork_install.parse_config_text(text)
                         ["LEGWORK_SESSION_ARGS"], "--model opus --effort high")

    def test_wizard_keeps_manual(self):
        self.assertEqual(legwork_install.carried_settings(
            {"LEGWORK_SWITCHBOARD": "manual"}), [("LEGWORK_SWITCHBOARD", "manual")])

    def test_upgrade_config_rewrites_an_old_switchboard(self):
        text = (f"LEGWORK_DIR=/a\n# LEGWORK_SWITCHBOARD={OLD}\n"
                f"LEGWORK_SWITCHBOARD={OLD}\n{install.RETIRED_KEYS[0]}=work\n"
                "LEGWORK_OWN_OWNERS=me\n")
        out, changed = install.upgrade_config(text)
        self.assertEqual(changed, ("LEGWORK_SWITCHBOARD", install.RETIRED_KEYS[0]))
        self.assertEqual(out, (f"LEGWORK_DIR=/a\n# LEGWORK_SWITCHBOARD={OLD}\n"
                               "LEGWORK_SWITCHBOARD=claude\nLEGWORK_OWN_OWNERS=me\n"))

    def test_upgrade_config_leaves_a_current_config_alone(self):
        for text in ("", "LEGWORK_SWITCHBOARD=manual\n",
                     "LEGWORK_SWITCHBOARD=claude\nA=1\n"):
            self.assertEqual(install.upgrade_config(text), (text, ()), text)

    def test_upgrade_config_names_only_what_it_changed(self):
        self.assertEqual(install.upgrade_config(f"LEGWORK_SWITCHBOARD={OLD}\n")[1],
                         ("LEGWORK_SWITCHBOARD",))
        self.assertEqual(install.upgrade_config(
            f"LEGWORK_SWITCHBOARD=claude\n{install.RETIRED_KEYS[0]}=work\n")[1],
            (install.RETIRED_KEYS[0],))

    def test_settings_are_documented(self):
        example = (REPO / "config.example").read_text(encoding="utf-8")
        self.assertIn("# LEGWORK_SESSION_ARGS=--model opus --effort high\n", example)
        self.assertIn("# LEGWORK_SWITCHBOARD_HEALTH=1\n", example)
        self.assertIn("# LEGWORK_SWITCHBOARD=claude\n", example)
        doc = (REPO / "CONFIG.md").read_text(encoding="utf-8")
        rows = {m.group(1): m.group(2) for m in re.finditer(
            r"^\| `(LEGWORK_[A-Z_]+)` \| ([^|]+) \|", doc, re.M)}
        self.assertEqual(rows["LEGWORK_SWITCHBOARD"].strip(), "`claude`")
        self.assertEqual(rows["LEGWORK_SESSION_ARGS"].strip(),
                         "`--model opus --effort high`")
        for key in legwork_install.ORCHESTRATION_KEYS:
            self.assertIn(key, rows, key)

    def test_render_config_without_carried_is_unchanged(self):
        v = {"level": 1, "legwork_dir": "/a"}
        self.assertNotIn("Orchestration", legwork_install.render_config(v))


class HookTests(unittest.TestCase):
    def test_usage_guard_hook_is_added_once_and_repointed(self):
        settings = {"model": "opus", "hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "other.sh"}]}]}}
        once = install.merge_usage_guard_hook(settings, "/old/usage-guard")
        twice = install.merge_usage_guard_hook(once, "/new/usage-guard")
        ups = twice["hooks"]["UserPromptSubmit"]
        self.assertEqual(len(ups), 2)
        self.assertEqual(ups[1]["hooks"][0],
                         {"type": "command", "timeout": 5,
                          "command": install.usage_guard_command("/new/usage-guard")})
        ptu = twice["hooks"]["PostToolUse"]
        self.assertEqual(len(ptu), 1)
        self.assertEqual(ptu[0]["matcher"], "*")
        self.assertEqual(twice["model"], "opus")
        self.assertNotIn("usage-guard", json.dumps(settings))

    def test_usage_guard_command_quotes_paths_with_spaces(self):
        self.assertEqual(install.usage_guard_command("/a b/usage-guard", "/py"),
                         "/py '/a b/usage-guard'")

    def test_usage_guard_command_survives_bash_on_windows(self):
        # Claude Code runs hooks through Git Bash on Windows. Bare, the
        # backslashes were eaten ("E:devx...: command not found"); quoted
        # they are literal. The interpreter is spelled out because the
        # script's python3 shebang is the Store stub on stock Windows.
        cmd = install.usage_guard_command(r"E:\dev\x\usage-guard", r"C:\Py\python.exe")
        self.assertEqual(cmd, r"'C:\Py\python.exe' 'E:\dev\x\usage-guard'")

    def test_pane_notify_hook_is_added_once_and_repointed(self):
        settings = {"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "other.sh"}]}]}}
        once = install.merge_pane_notify_hook(settings, "/old/bin/pane-notify")
        twice = install.merge_pane_notify_hook(once, "/new/bin/pane-notify")
        stop = twice["hooks"]["Stop"]
        self.assertEqual(len(stop), 2)
        self.assertEqual(stop[0]["hooks"][0]["command"], "other.sh")
        self.assertEqual(stop[1]["hooks"], [{"type": "command", "timeout": 10,
                                             "command": "/new/bin/pane-notify"}])
        self.assertEqual(twice["hooks"]["Notification"],
                         [{"hooks": [{"type": "command", "timeout": 10,
                                      "command": "/new/bin/pane-notify"}]}])
        self.assertNotIn("pane-notify", json.dumps(settings))

    def test_pane_notify_command_on_posix_is_the_bare_script(self):
        with mock.patch.object(install, "script_python", return_value=None):
            self.assertEqual(install.pane_notify_command("/engine/bin"),
                             "/engine/bin/pane-notify")

    def test_pane_notify_command_on_windows_names_the_interpreter(self):
        # Same reason as usage-guard: Claude Code runs hooks through Git
        # Bash, and python3 there is the Store stub.
        with mock.patch.object(install, "Path", PureWindowsPath):
            cmd = install.pane_notify_command(
                r"E:\dev\engine\bin", python=r"C:\Program Files\Py\python.exe")
        self.assertEqual(cmd, "'C:/Program Files/Py/python.exe' "
                              "E:/dev/engine/bin/pane-notify")


class PlatformCheckTests(unittest.TestCase):
    def test_platform_kind(self):
        self.assertEqual(install.platform_kind("darwin"), "mac")
        self.assertEqual(install.platform_kind("win32"), "windows")
        self.assertEqual(install.platform_kind("linux"), "other")

    def test_health_watcher_is_skipped_off_mac_and_windows(self):
        skip, _ = install.health_check("other", every_tool, "claude")
        self.assertIn("macOS", skip)
        self.assertIn("Windows", skip)

    def test_health_watcher_is_skipped_for_a_manual_switchboard(self):
        skip, _ = install.health_check("mac", every_tool, "manual")
        self.assertIn("manual", skip)

    def test_health_watcher_needs_herdr_and_the_scheduler(self):
        skip, _ = install.health_check("windows", no_tool, "claude")
        self.assertIn("herdr", skip)
        for kind, tool in (("mac", "launchctl"), ("windows", "schtasks")):
            skip, _ = install.health_check(
                kind, lambda n, t=tool: None if n == t else every_tool(n), "claude")
            self.assertIn(tool, skip, kind)

    def test_health_watcher_notes_a_missing_claude_and_the_trust_question(self):
        for kind in ("mac", "windows"):
            skip, notes = install.health_check(
                kind, lambda n: None if n == "claude" else every_tool(n), "claude")
            self.assertIsNone(skip, kind)
            self.assertTrue(any("claude" in n and "PATH" in n for n in notes), kind)
            self.assertTrue(any("trusted" in n for n in notes), kind)
        skip, notes = install.health_check("windows", every_tool, "claude")
        self.assertIsNone(skip)
        self.assertFalse(any("PATH" in n for n in notes))

    def test_pane_notify_is_skipped_off_mac_and_windows(self):
        skip, _ = install.pane_notify_check("other", every_tool)
        self.assertIn("macOS", skip)

    def test_pane_notify_notes(self):
        skip, notes = install.pane_notify_check("mac", no_tool, exists=lambda _: False)
        self.assertIsNone(skip)
        text = "\n".join(notes)
        self.assertIn("terminal-notifier", text)
        self.assertIn("herdr", text)
        self.assertIn('delivery = "off"', text)
        self.assertIn("reload-config", text)
        skip, notes = install.pane_notify_check("windows", every_tool)
        self.assertIsNone(skip)
        text = "\n".join(notes)
        self.assertNotIn("terminal-notifier", text)
        self.assertIn("%APPDATA%", text)


class InstallRunTests(unittest.TestCase):
    """A real run into scratch dirs: nothing under the real home is touched."""

    def run_install(self, tmp, argv, config="LEGWORK_DIR=/x\n", kind="windows",
                    which=every_tool, rules=True):
        legwork, claude = tmp / "legwork", tmp / "claude"
        legwork.mkdir(exist_ok=True)
        cfg = legwork / "config"
        if config is not None and not cfg.exists():
            cfg.write_text(config)
        if rules:
            (legwork / "house-rules.md").write_text("# House rules\n\n- Rule A.\n")
        env = {k: v for k, v in os.environ.items()
               if k not in ("LEGWORK_SWITCHBOARD", "LEGWORK_DIR")}
        env.update(HOME=str(tmp), USERPROFILE=str(tmp), LEGWORK_CONFIG=str(cfg))
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(install, "platform_kind", return_value=kind), \
                mock.patch.object(install.shutil, "which", which), \
                redirect_stdout(out):
            rc = install.main(argv + ["--claude-dir", str(claude),
                                      "--legwork-dir", str(legwork)])
        self.assertEqual(rc, 0)
        return legwork, claude, out.getvalue()

    def test_full_install_into_scratch_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            legwork, claude, _ = self.run_install(Path(tmp), [
                "--with", "shipping,fanout,switchboard,usage-guard,"
                          "switchboard-health,pane-notify",
                "--set", "LEGWORK_BUGBOT_OWNERS=example-org"])
            self.assertTrue((claude / "commands" / "ship.md").is_file())
            self.assertTrue((claude / "skills" / "fanout" / "SKILL.md").is_file())
            self.assertTrue((claude / "skills" / "roadmap-planning" / "SKILL.md").is_file())
            settings = json.loads((claude / "settings.json").read_text())
            self.assertIn("PostToolUse", settings["hooks"])
            for event in ("Stop", "Notification"):
                hooks = [h for e in settings["hooks"][event] for h in e["hooks"]]
                self.assertEqual(len(hooks), 1, event)
                self.assertTrue(hooks[0]["command"].endswith(
                    f"{(ORCH / 'bin').as_posix()}/pane-notify"), hooks[0])
                self.assertEqual(hooks[0]["timeout"], 10)
            # On Windows each script line starts with the interpreter.
            python = install.script_python()
            run = f"{shlex.quote(Path(python).as_posix())} " if python else ""
            fanout = (claude / "skills" / "fanout" / "SKILL.md").read_text()
            self.assertIn(f"`{run}{(ORCH / 'bin').as_posix()}/bugbot-wait", fanout)
            self.assertNotIn("{{", fanout)
            skill = (claude / "skills" / "switchboard" / "SKILL.md").read_text()
            self.assertIn(f"`{run}{(ORCH / 'bin').as_posix()}/switchboard-wait", skill)
            self.assertIn(f"`{legwork.resolve().as_posix()}/config`", skill)
            # Not the default ~/.claude, so new panes start under it.
            self.assertIn(f"CLAUDE_CONFIG_DIR={claude.as_posix()} ", skill)
            cfg = (legwork / "config").read_text()
            for line in ("LEGWORK_DIR=/x", "LEGWORK_BUGBOT_OWNERS=example-org",
                         "LEGWORK_ORCHESTRATION=1", "LEGWORK_SWITCHBOARD_HEALTH=1"):
                self.assertIn(line + "\n", cfg)

    def test_rerun_registers_each_hook_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(2):
                _, claude, _ = self.run_install(
                    Path(tmp), ["--with", "usage-guard,pane-notify"])
            settings = json.loads((claude / "settings.json").read_text())
            for event in ("Stop", "Notification", "UserPromptSubmit", "PostToolUse"):
                self.assertEqual(len(settings["hooks"][event]), 1, event)

    def test_manual_switchboard_skips_the_session_pieces(self):
        with tempfile.TemporaryDirectory() as tmp:
            legwork, claude, out = self.run_install(
                Path(tmp), ["--with", "switchboard,switchboard-health"],
                config="LEGWORK_SWITCHBOARD=manual\n")
            self.assertTrue((claude / "skills" / "switchboard-protocol" / "SKILL.md").is_file())
            self.assertTrue((claude / "skills" / "roadmap-planning" / "SKILL.md").is_file())
            self.assertFalse((claude / "skills" / "switchboard").exists())
            skips = [ln for ln in out.splitlines() if ln.startswith("skip the ")]
            self.assertEqual(len(skips), 1, out)
            self.assertIn("switchboard skill", skips[0])
            self.assertNotIn("roadmap-planning", skips[0])
            self.assertIn("skip switchboard-health", out)
            self.assertNotIn("LEGWORK_SWITCHBOARD_HEALTH", (legwork / "config").read_text())

    def test_set_manual_wins_over_the_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            legwork, claude, _ = self.run_install(
                Path(tmp), ["--with", "switchboard", "--set", "LEGWORK_SWITCHBOARD=manual"])
            self.assertFalse((claude / "skills" / "switchboard").exists())
            self.assertIn("LEGWORK_SWITCHBOARD=manual\n", (legwork / "config").read_text())

    def test_an_old_config_still_installs_and_is_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            legwork, claude, out = self.run_install(
                Path(tmp), ["--with", "switchboard"],
                config=(f"LEGWORK_SWITCHBOARD={OLD}\n"
                        f"{install.RETIRED_KEYS[0]}=work\nLEGWORK_OWN_OWNERS=me\n"))
            self.assertTrue((claude / "skills" / "switchboard" / "SKILL.md").is_file())
            cfg = (legwork / "config").read_text()
            self.assertIn("LEGWORK_SWITCHBOARD=claude\n", cfg)
            self.assertIn("LEGWORK_OWN_OWNERS=me\n", cfg)
            self.assertNotIn(install.RETIRED_KEYS[0], cfg)
            notes = [ln for ln in out.splitlines()
                     if ln.startswith("note:") and "LEGWORK_SWITCHBOARD" in ln]
            self.assertEqual(len(notes), 1, out)
            self.assertNotIn(OLD, out.lower())

    def test_linux_skips_the_watcher_and_the_notifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            legwork, claude, out = self.run_install(
                Path(tmp), ["--with", "switchboard-health,pane-notify"], kind="other")
            self.assertIn("skip switchboard-health", out)
            self.assertIn("skip pane-notify", out)
            self.assertFalse((claude / "settings.json").exists())
            self.assertNotIn("LEGWORK_SWITCHBOARD_HEALTH", (legwork / "config").read_text())

    def test_house_rules_note_counts_planning_sessions(self):
        # The switchboard piece installs roadmap-planning in both modes, and
        # a planning session commits and opens a docs PR, so it follows the
        # house rules.
        for config in ("LEGWORK_DIR=/x\n", "LEGWORK_SWITCHBOARD=manual\n"):
            with tempfile.TemporaryDirectory() as tmp:
                _, _, out = self.run_install(Path(tmp), ["--with", "switchboard"],
                                             config=config, rules=False)
                self.assertIn("house-rules.md", out, config)
                self.assertIn("planning sessions", out, config)
        with tempfile.TemporaryDirectory() as tmp:
            _, _, out = self.run_install(Path(tmp), ["--with", "shipping"], rules=False)
            self.assertIn("house-rules.md", out)

    def test_old_piece_and_flag_are_skipped_with_one_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, claude, out = self.run_install(Path(tmp), [
                "--with", f"shipping,{OLD}", f"--{OLD}-profile", "work"])
            self.assertTrue((claude / "commands" / "ship.md").is_file())
            notes = [ln for ln in out.splitlines() if OLD in ln.lower()]
            self.assertEqual(len(notes), 1, out)
            self.assertTrue(notes[0].startswith("skip "), notes)

    def test_set_switchboard_writes_only_claude_or_manual(self):
        for given, written in ((OLD, "claude"), ("Manual", "manual"), ("claude", "claude")):
            with tempfile.TemporaryDirectory() as tmp:
                legwork, _, out = self.run_install(
                    Path(tmp), ["--set", f"LEGWORK_SWITCHBOARD={given}"])
                cfg = (legwork / "config").read_text()
                self.assertIn(f"LEGWORK_SWITCHBOARD={written}\n", cfg)
                self.assertNotIn(OLD, cfg.lower())
                self.assertEqual(OLD in out.lower(), given == OLD, out)
        with tempfile.TemporaryDirectory() as tmp, \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_install(Path(tmp), ["--set", "LEGWORK_SWITCHBOARD=other"])

    def test_upgrade_note_names_only_what_changed(self):
        for config, profile, mode in (
                (f"LEGWORK_SWITCHBOARD={OLD}\n", False, True),
                (f"LEGWORK_SWITCHBOARD=claude\n{install.RETIRED_KEYS[0]}=w\n", True, False)):
            with tempfile.TemporaryDirectory() as tmp:
                _, _, out = self.run_install(Path(tmp), ["--with", "shipping"],
                                             config=config)
                note = [ln for ln in out.splitlines()
                        if ln.startswith("note:") and "no longer ships" in ln]
                self.assertEqual(len(note), 1, out)
                self.assertEqual("profile key" in note[0], profile, note)
                self.assertEqual("now reads claude" in note[0], mode, note)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with mock.patch.dict(os.environ, {"HOME": str(tmp),
                                              "LEGWORK_CONFIG": str(tmp / "config")}), \
                    redirect_stdout(io.StringIO()):
                install.main(["--with", "shipping,usage-guard", "--dry-run",
                              "--claude-dir", str(tmp / "claude"),
                              "--set", "A=1"])
            self.assertEqual(list(tmp.iterdir()), [])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = load(ORCH / "settings.py", "orchestration_settings")

    def test_lookup_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "explicit").write_text("K=from-explicit\n")
            (tmp / "q").mkdir()
            (tmp / "q" / "config").write_text("K=from-queue\nJ=~/j\n")
            s = self.settings.setting
            self.assertEqual(s("K", env={"K": "env"}), "env")
            self.assertEqual(s("K", env={"LEGWORK_CONFIG": str(tmp / "explicit"),
                                         "LEGWORK_DIR": str(tmp / "q")}), "from-explicit")
            self.assertEqual(s("K", env={"LEGWORK_DIR": str(tmp / "q")}), "from-queue")
            self.assertEqual(Path(s("J", env={"LEGWORK_DIR": str(tmp / "q")})),
                             Path.home() / "j")
            self.assertEqual(s("NOPE", "d", env={"LEGWORK_DIR": str(tmp / "q")}), "d")


class BugbotOwnersTests(unittest.TestCase):
    def setUp(self):
        self.mod = load(ORCH / "bin" / "bugbot-wait", "bugbot_wait")

    def test_owners_from_environment(self):
        with mock.patch.dict(os.environ, {"LEGWORK_BUGBOT_OWNERS": " Org-A, org-b ,"}):
            self.assertEqual(self.mod.bugbot_owners(), {"org-a", "org-b"})

    def test_no_owner_set_means_no_bugbot_and_nothing_posted(self):
        calls = []

        def gh(*args):
            calls.append(args)
            return "someone/repo\n"

        env = {k: v for k, v in os.environ.items()
               if k not in ("LEGWORK_BUGBOT_OWNERS", "LEGWORK_DIR")}
        env["LEGWORK_CONFIG"] = os.devnull
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.mod, "gh", gh), \
                mock.patch.object(sys, "argv", ["bugbot-wait", "7", "--trigger"]), \
                redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as stop:
                self.mod.main()
        self.assertEqual(stop.exception.code, 4)
        self.assertEqual(json.loads(out.getvalue())["state"], "NO_BUGBOT")
        self.assertEqual(calls, [("repo", "view", "--json", "nameWithOwner",
                                  "-q", ".nameWithOwner")])


class UsageGuardTests(unittest.TestCase):
    def test_legwork_usage_cache_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "usage.json"
            cache.write_text(json.dumps({
                "weeklyUsage": 97, "weeklyResetAt": "2099-01-01T00:00:00Z"}))
            env = dict(os.environ, LEGWORK_USAGE_CACHE=str(cache),
                       USAGE_GUARD_STAMPS=str(Path(tmp) / "stamps"))
            env.pop("USAGE_GUARD_CACHE", None)
            out = subprocess.run(
                [sys.executable, str(ORCH / "bin" / "usage-guard"), "--check"],
                capture_output=True, text=True, env=env, timeout=30).stdout
        self.assertIn("weekly all-models limit", out)
        self.assertIn("PAUSE", out)


class GenericCopyTests(unittest.TestCase):
    """The public copies carry no one's machine: every path is a setting or a
    placeholder, never a home folder copied in from a live setup."""

    LEAKS = re.compile(r"/Users/|/home/[a-z]|C:\\\\Users")

    def test_no_machine_paths(self):
        files = [p for p in ORCH.rglob("*") if p.is_file()
                 and not any(part.startswith((".", "__")) for part in p.relative_to(ORCH).parts)]
        files += [REPO / "core" / "skills" / "legwork-onboard" / "SKILL.md",
                  REPO / "house-rules.example.md"]
        for path in files:
            text = path.read_text(encoding="utf-8")
            for m in self.LEAKS.finditer(text):
                self.fail(f"{path.relative_to(REPO)}: {m.group(0)!r}")


class RetiredSwitchboardTests(unittest.TestCase):
    """The switchboard legwork no longer ships is named nowhere a user reads,
    so nothing tells anyone to install it. The one exception is the
    compatibility line in orchestration/install.py."""

    def test_named_only_on_the_compatibility_line(self):
        try:
            listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                                    capture_output=True, timeout=60,
                                    check=True).stdout
        except (OSError, subprocess.SubprocessError):
            self.skipTest("not a git checkout")
        hits = []
        for rel in filter(None, listed.decode("utf-8").split("\0")):
            path = REPO / rel
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line in text.splitlines():
                if OLD in line.lower():
                    hits.append((rel, line.strip()))
        self.assertEqual(hits, [("orchestration/install.py",
                                 f'RETIRED_KEYS = ("{install.RETIRED_KEYS[0]}",)')])
        self.assertFalse((ORCH / OLD).exists())

    def test_house_rules_example_names_no_switchboard_reader(self):
        text = (REPO / "house-rules.example.md").read_text(encoding="utf-8")
        self.assertNotIn("SOUL", text)
        self.assertNotIn("switchboard", text)
        # Planning sessions commit and open a docs PR, so they follow them.
        self.assertIn("roadmap-planning", text)

    def test_smoke_test_cleans_up_before_the_panes_close(self):
        # Left in the state file, smoke keeps the health watcher restarting
        # a switchboard for a throwaway repo.
        text = (ORCH / "docs" / "smoke-test.md").read_text(encoding="utf-8")
        self.assertIn("switchboard-state remove smoke", text)
        self.assertIn("switchboard-health-ctl off", text)
        self.assertLess(text.index("switchboard-state remove smoke"),
                        text.index("## Known gotcha"))


class OnboardTests(unittest.TestCase):
    def setUp(self):
        self.text = (REPO / "core" / "skills" / "legwork-onboard" / "SKILL.md").read_text(
            encoding="utf-8")

    def test_onboard_lists_every_piece(self):
        for piece in install.PIECES:
            self.assertIn(f"`{piece}`", self.text, piece)

    def test_pane_notify_hand_over_applies_with_a_manual_switchboard_too(self):
        # pane-notify is offered with Herdr either way, so its Herdr config
        # step must not sit under the Claude Code switchboard bullet.
        at = self.text.index("with `pane-notify`: switch Herdr")
        bullet = self.text.rfind("\n- ", 0, at)
        self.assertNotIn("Claude Code switchboard", self.text[bullet:at])

    def test_onboard_offers_a_claude_or_manual_switchboard(self):
        self.assertIn("`LEGWORK_SWITCHBOARD=claude` or `manual`", self.text)
        self.assertIn("/switchboard", self.text)


class WindowsFormTests(unittest.TestCase):
    """Commands run by hand or by /onboard must work on stock Windows, where
    `python3`, and so the scripts' own first line, is the Store stub."""

    def test_onboard_runs_python_through_the_detected_interpreter(self):
        text = (REPO / "core" / "skills" / "legwork-onboard" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("`<python>` below", text)
        for m in re.finditer(r"python3 orchestration/|`orchestration/bin/[\w-]+", text):
            self.fail(f"bare call in the onboard skill: {m.group(0)!r}")

    def test_docs_give_hand_run_commands_a_windows_form(self):
        command = re.compile(r"`(?:orchestration/)?bin/[\w-]+ -|^python3 orchestration/")
        for path in [ORCH / "README.md", *sorted((ORCH / "docs").glob("*.md"))]:
            lines = path.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if command.search(line.strip()):
                    near = "\n".join(lines[i:i + 5])
                    self.assertIn("Windows", near, f"{path.name}:{i + 1}: {line}")


class DocCommandTests(unittest.TestCase):
    """The orchestration docs name only commands, flags and pieces the
    scripts and the installer have: doc-versus-code drift is a defect."""

    DOCS = [ORCH / "README.md", *sorted((ORCH / "docs").glob("*.md"))]

    def snippets(self, path):
        """Backtick spans and code block lines: the places commands go."""
        text = path.read_text(encoding="utf-8")
        found = re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", text, flags=re.S))
        for block in re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S):
            found += block.splitlines()
        return found

    def usage(self, name):
        """The words and flags in a bin script's Usage lines, or in its
        whole docstring when it has none."""
        text = (ORCH / "bin" / name).read_text(encoding="utf-8")
        doc = text.split('"""')[1]
        lines = doc[doc.index("Usage:"):].split("\n\n", 1)[0] if "Usage:" in doc else doc
        return set(re.findall(r"--?[\w-]+|[a-z][\w-]*", lines))

    def test_scripts_flags_and_subcommands_exist(self):
        scripts = {p.name for p in (ORCH / "bin").iterdir() if p.is_file()}
        for path in self.DOCS:
            for snippet in self.snippets(path):
                for m in re.finditer(r"bin/([\w-]+)", snippet):
                    self.assertIn(m.group(1), scripts, f"{path.name}: {snippet}")
                for name in scripts:
                    for m in re.finditer(rf"(?:^|[/\s]){re.escape(name)}\s+(--?[\w-]+|[a-z][\w-]*)",
                                         snippet):
                        self.assertIn(m.group(1), self.usage(name),
                                      f"{path.name}: {snippet}")

    def test_install_flags_and_pieces_exist(self):
        source = (ORCH / "install.py").read_text(encoding="utf-8")
        flags = set(re.findall(r'add_argument\("(--[\w-]+)"', source))
        for path in self.DOCS:
            for snippet in self.snippets(path):
                if "install.py" not in snippet:
                    continue
                for flag in re.findall(r"(?<![\w-])--[\w-]+", snippet):
                    self.assertIn(flag, flags | {"--help"}, f"{path.name}: {snippet}")
                for m in re.finditer(r"--with ([\w,-]+)", snippet):
                    for piece in m.group(1).split(","):
                        self.assertIn(piece, set(install.PIECES) | {"all"},
                                      f"{path.name}: {snippet}")

    def test_session_start_lines_use_the_session_args_setting(self):
        # The health watcher restarts the switchboard with
        # LEGWORK_SESSION_ARGS, so a start line that spells out the default
        # would run a different model from the one the user set.
        default = re.compile(r"(?:\bclaude|(?:^|\s)--)\s+--model opus --effort high")
        for path in self.DOCS:
            for snippet in self.snippets(path):
                self.assertIsNone(default.search(snippet), f"{path.name}: {snippet}")

    def test_switchboard_setup_is_linked_where_users_look(self):
        for path in (REPO / "SETUP.md", REPO / "README.md", REPO / "CONFIG.md",
                     ORCH / "README.md", ORCH / "docs" / "first-run.md"):
            self.assertIn("docs/switchboard-setup.md" if path.parent == REPO
                          or path.name == "README.md" else "switchboard-setup.md",
                          path.read_text(encoding="utf-8"), path.name)

    def test_switchboard_setup_covers_both_platforms(self):
        text = (ORCH / "docs" / "switchboard-setup.md").read_text(encoding="utf-8")
        for needle in ('delivery = "off"', 'claude = "off"', "herdr server reload-config",
                       "~/.config/herdr/config.toml", r"%APPDATA%\herdr\config.toml",
                       "brew install terminal-notifier", "Windows PowerShell",
                       "launchd", "Task Scheduler", "/switchboard resume",
                       "LEGWORK_SESSION_ARGS", "LEGWORK_SWITCHBOARD_DIR",
                       "context:", "## Manual mode", "LEGWORK_DIR", "LEGWORK_CONFIG",
                       "Git Bash", "SWITCHBOARD_STATE"):
            self.assertIn(needle, text)

    def test_flow_diagram_shows_the_watcher_banner(self):
        text = (ORCH / "README.md").read_text(encoding="utf-8")
        diagram = text.split("## How it flows", 1)[1].split("```")[1]
        self.assertIn("banner", diagram)


if __name__ == "__main__":
    unittest.main()
