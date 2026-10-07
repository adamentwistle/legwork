"""Tests for the optional orchestration layer under orchestration/.

Covers the installer's pure functions, the settings the scripts read
(LEGWORK_BUGBOT_OWNERS, LEGWORK_USAGE_CACHE), the wizard carrying those
settings through a config rewrite, and a scan that keeps the public copies
free of anything tied to one person's setup.
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
from contextlib import redirect_stdout
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


class PiecesTests(unittest.TestCase):
    def test_parse_pieces_keeps_canonical_order(self):
        self.assertEqual(install.parse_pieces("hermes, shipping"),
                         ["shipping", "hermes"])
        self.assertEqual(install.parse_pieces("all"), list(install.PIECES))
        self.assertEqual(install.parse_pieces(""), [])

    def test_parse_pieces_rejects_unknown(self):
        with self.assertRaises(ValueError):
            install.parse_pieces("shipping,runner")

    def test_plan_copies_mirrors_layout_and_sources_exist(self):
        dest = Path("/tmp/fake-claude")
        pairs = install.plan_copies(["shipping", "fanout", "switchboard"], dest)
        got = {d.relative_to(dest).as_posix() for _, d in pairs}
        self.assertEqual(got, {
            "commands/ship.md", "commands/bugbot.md", "commands/fanout.md",
            "skills/fanout/SKILL.md", "skills/switchboard-protocol/SKILL.md"})
        for src, _ in pairs:
            self.assertTrue(src.is_file(), src)

    def test_claude_home_prefers_explicit_then_env(self):
        self.assertEqual(install.claude_home("/x", {"CLAUDE_CONFIG_DIR": "/y"}),
                         Path("/x"))
        self.assertEqual(install.claude_home(None, {"CLAUDE_CONFIG_DIR": "/y"}),
                         Path("/y"))
        self.assertEqual(install.claude_home(None, {}), Path.home() / ".claude")

    def test_hermes_home(self):
        root = Path("/h")
        self.assertEqual(install.hermes_home("default", root), root)
        self.assertEqual(install.hermes_home("work", root),
                         root / "profiles" / "work")


class HermesSkillTests(unittest.TestCase):
    def setUp(self):
        self.text = (ORCH / "hermes" / "switchboard" / "SKILL.md").read_text()

    def test_render_fills_every_placeholder(self):
        out = install.render(self.text, "/srv/legwork", "/srv/legwork/orchestration/bin", "/c/dir")
        self.assertNotIn("{{", out)
        self.assertIn("/srv/legwork/orchestration/bin/switchboard-wait", out)
        self.assertIn("--env\n   CLAUDE_CONFIG_DIR=/c/dir`", out)

    def test_render_without_config_dir_has_no_env_flag(self):
        out = install.render(self.text, "/srv/legwork")
        self.assertNotIn("CLAUDE_CONFIG_DIR", out)
        self.assertIn("--cwd <repo>`", out)

    def test_every_installed_file_renders_clean(self):
        for src, _ in install.plan_copies(list(install.PIECES), Path("/c")):
            out = install.render(src.read_text(), "/q", "/engine/bin")
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
        for src in sources + [ORCH / "hermes" / "switchboard" / "SKILL.md"]:
            text = src.read_text()
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

    def test_render_config_without_carried_is_unchanged(self):
        v = {"level": 1, "legwork_dir": "/a"}
        self.assertNotIn("Orchestration", legwork_install.render_config(v))


class HookAndSoulTests(unittest.TestCase):
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

    def test_soul_block_replaces_rather_than_appends(self):
        once = install.merge_soul("Be kind.\n", "- rule one")
        twice = install.merge_soul(once, "- rule two")
        self.assertEqual(twice.count(install.SOUL_START), 1)
        self.assertIn("- rule two", twice)
        self.assertNotIn("- rule one", twice)
        self.assertTrue(twice.startswith("Be kind.\n\n"))

    def test_house_rules_lines_joins_wrapped_items(self):
        text = "# H\n\nIntro prose.\n\n- One rule\n  wrapped here.\n- Two.\n\nAfter.\n"
        self.assertEqual(install.house_rules_lines(text),
                         "- One rule wrapped here.\n- Two.")

    def test_house_rules_lines_indented_line_after_a_gap_is_not_a_continuation(self):
        self.assertEqual(install.house_rules_lines("- a\n\n  b\n- c\n"), "- a\n- c")

    def test_unmarked_preferences_block_is_spotted(self):
        self.assertTrue(install.unmarked_preferences("Standing preferences:\n- x\n"))
        marked = install.merge_soul("", "- x")
        self.assertFalse(install.unmarked_preferences(marked))

    def test_house_rules_lines_takes_list_items_only(self):
        text = (REPO / "house-rules.example.md").read_text()
        lines = install.house_rules_lines(text).splitlines()
        self.assertGreater(len(lines), 2)
        self.assertTrue(all(line.startswith("- ") for line in lines))


class InstallRunTests(unittest.TestCase):
    """A real run into scratch dirs: nothing under the real home is touched."""

    def test_full_install_into_scratch_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            legwork, claude, hermes = tmp / "legwork", tmp / "claude", tmp / "hermes"
            legwork.mkdir()
            (legwork / "config").write_text("LEGWORK_DIR=/x\n")
            (legwork / "house-rules.md").write_text("# House rules\n\n- Rule A.\n")
            env = {"HOME": str(tmp), "LEGWORK_CONFIG": str(legwork / "config")}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(install, "hermes_home",
                                      lambda p: hermes / "profiles" / p), \
                    redirect_stdout(io.StringIO()):
                rc = install.main([
                    "--with", "shipping,fanout,switchboard,usage-guard,hermes",
                    "--claude-dir", str(claude), "--legwork-dir", str(legwork),
                    "--hermes-profile", "work",
                    "--set", "LEGWORK_BUGBOT_OWNERS=example-org"])
            self.assertEqual(rc, 0)
            self.assertTrue((claude / "commands" / "ship.md").is_file())
            self.assertTrue((claude / "skills" / "fanout" / "SKILL.md").is_file())
            settings = json.loads((claude / "settings.json").read_text())
            self.assertIn("PostToolUse", settings["hooks"])
            # On Windows each script line starts with the interpreter.
            python = install.script_python()
            run = f"{shlex.quote(Path(python).as_posix())} " if python else ""
            fanout = (claude / "skills" / "fanout" / "SKILL.md").read_text()
            self.assertIn(f"`{run}{(ORCH / 'bin').as_posix()}/bugbot-wait", fanout)
            self.assertNotIn("{{", fanout)
            skill = (hermes / "profiles" / "work" / install.HERMES_SKILL).read_text()
            self.assertIn(f"`{run}{(ORCH / 'bin').as_posix()}/switchboard-wait", skill)
            self.assertIn(f"`{legwork.resolve().as_posix()}/config`", skill)
            self.assertIn(f"CLAUDE_CONFIG_DIR={claude}", skill)
            soul = (hermes / "profiles" / "work" / "SOUL.md").read_text()
            self.assertIn("- Rule A.", soul)
            cfg = (legwork / "config").read_text()
            for line in ("LEGWORK_DIR=/x", "LEGWORK_BUGBOT_OWNERS=example-org",
                         "LEGWORK_HERMES_PROFILE=work", "LEGWORK_ORCHESTRATION=1"):
                self.assertIn(line + "\n", cfg)

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


if __name__ == "__main__":
    unittest.main()
