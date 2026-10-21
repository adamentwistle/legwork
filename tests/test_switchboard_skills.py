"""Tests for the Claude Code skills under orchestration/skills.

Generic checks run over every skill there: each renders with no placeholder
left, names its own folder, calls only scripts this checkout ships, and
carries no work names. The rest pin what the switchboard and
roadmap-planning skills must say, so a later edit cannot drop a rule
quietly: how the switchboard keeps its own token cost down, which settings
the owner and Linear rules read, and the Windows and Mac forms.
"""

import importlib.machinery
import importlib.util
import re
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
ORCH = REPO / "orchestration"
SKILLS = ORCH / "skills"


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


install = load(ORCH / "install.py", "orchestration_install_skills")

# Scripts a skill may call before the slice that adds them has landed.
PENDING_SCRIPTS = set()

# Generic shapes only, so no private name is written into a public test:
# home folders, a second Claude config folder, tracker issue ids, the user's
# own name where the skill should say "the user", and the em dash.
WORK_NAMES = re.compile(
    r"\bAdam\b|/Users/|/home/[a-z]|C:\\Users|~/\.claude-[a-z]"
    r"|\b[A-Z]{2,5}-[0-9]+\b|" "\u2014", re.I)


def text(name):
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def frontmatter(body):
    m = re.match(r"---\n(.*?)\n---\n", body, re.S)
    return dict(line.split(": ", 1) for line in m.group(1).splitlines()
                if ": " in line) if m else {}


def flat(body):
    """The text with line breaks and runs of spaces as single spaces, so a
    phrase wrapped across lines still matches."""
    return re.sub(r"\s+", " ", body)


class EverySkillTests(unittest.TestCase):
    def skills(self):
        found = sorted(SKILLS.glob("*/SKILL.md"))
        self.assertTrue(found)
        return found

    def test_new_skills_exist(self):
        for name in ("switchboard", "roadmap-planning"):
            self.assertTrue((SKILLS / name / "SKILL.md").is_file(), name)

    def test_renders_with_no_placeholder_left(self):
        for path in self.skills():
            body = path.read_text(encoding="utf-8")
            for config_dir in (None, "/c/dir"):
                with mock.patch.object(install, "script_python", return_value=None):
                    out = install.render(body, "/q", "/engine/bin", config_dir)
                self.assertNotIn("{{", out, path)

    def test_name_matches_folder(self):
        for path in self.skills():
            meta = frontmatter(path.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("name"), path.parent.name, path)
            self.assertTrue(meta.get("description"), path)

    def test_calls_only_scripts_this_checkout_ships(self):
        shipped = {p.name for p in (ORCH / "bin").iterdir()} | PENDING_SCRIPTS
        for path in self.skills():
            for script in re.findall(r"\{\{LEGWORK_BIN\}\}/([\w.-]+)",
                                     path.read_text(encoding="utf-8")):
                self.assertIn(script, shipped, path)

    def test_no_work_names(self):
        for path in self.skills():
            for m in WORK_NAMES.finditer(path.read_text(encoding="utf-8")):
                self.fail(f"{path.relative_to(REPO)}: {m.group(0)!r}")

    def test_every_script_line_runs_through_the_interpreter_on_windows(self):
        # A short form like `switchboard-state add` is fine in prose, but
        # each script must appear once as a full {{LEGWORK_BIN}} line, which
        # render() turns into "<python> <bin>/<script>" on Windows.
        scripts = {p.name for p in (ORCH / "bin").iterdir()} | PENDING_SCRIPTS
        for name in ("switchboard", "roadmap-planning"):
            body = text(name)
            for script in scripts:
                if re.search(r"`" + re.escape(script) + r"[ `]", body):
                    self.assertIn("{{LEGWORK_BIN}}/" + script, body, (name, script))


class SwitchboardSkillTests(unittest.TestCase):
    def setUp(self):
        self.body = text("switchboard")
        self.flat = flat(self.body)

    def test_starts_only_from_the_slash_command(self):
        meta = frontmatter(self.body)
        self.assertEqual(meta.get("disable-model-invocation"), "true")
        self.assertIn("/switchboard resume", self.body)

    def test_no_reviewers_rule(self):
        self.assertNotRegex(self.flat, re.compile(r"no reviewers|reviewers", re.I))

    def test_scripts_go_through_the_placeholder(self):
        for script in ("switchboard-wait", "switchboard-answer",
                       "switchboard-state", "switchboard-health-ctl",
                       "usage-guard"):
            self.assertIn("{{LEGWORK_BIN}}/" + script, self.body, script)
        self.assertIn("{{LEGWORK_DIR}}", self.body)

    def test_state_file_lives_in_the_switchboard_dir(self):
        self.assertIn("LEGWORK_SWITCHBOARD_DIR", self.body)
        self.assertIn("state.md", self.body)

    def test_cycles_itself_at_300k_or_60_percent(self):
        self.assertIn("300,000 tokens or 60% of the window, whichever comes first",
                      self.flat)
        self.assertIn("after every event", self.flat)
        self.assertIn("switchboard-state cycle", self.body)

    def test_does_no_work_on_a_timeout_or_a_no_op_wake(self):
        section = self.flat.split("## Waking for nothing", 1)
        self.assertEqual(len(section), 2, "no 'Waking for nothing' section")
        part = section[1].split(" ## ", 1)[0]
        for phrase in ("timeout", "no-op", "straight back into the wait"):
            self.assertIn(phrase, part)

    def test_opens_orchestrator_panes_with_pane_env_and_session_args(self):
        self.assertIn("--cwd <repo>{{PANE_ENV}} --env DISABLE_AUTO_UPDATE=true",
                      self.body)
        self.assertIn("LEGWORK_SESSION_ARGS", self.body)
        self.assertIn("--model opus --effort high", self.body)

    def test_pane_env_renders_inside_the_tab_command(self):
        with mock.patch.object(install, "script_python", return_value=None):
            on = flat(install.render(self.body, "/q", "/b", "/c/dir"))
            off = flat(install.render(self.body, "/q", "/b"))
        self.assertIn("--cwd <repo> --env CLAUDE_CONFIG_DIR=/c/dir --env "
                      "DISABLE_AUTO_UPDATE=true", on)
        self.assertIn("--cwd <repo> --env DISABLE_AUTO_UPDATE=true", off)

    def test_health_watcher_only_when_installed_on_windows_and_mac(self):
        self.assertIn("LEGWORK_SWITCHBOARD_HEALTH", self.body)
        for word in ("Windows", "Mac"):
            self.assertIn(word, self.body)
        self.assertRegex(self.flat, r"skip every `?switchboard-health-ctl")

    def test_owner_and_linear_rules_read_the_settings(self):
        for key in ("LEGWORK_OWN_OWNERS", "LEGWORK_LINEAR_TEAM",
                    "LEGWORK_LINEAR_TEAM_KEY"):
            self.assertIn(key, self.body)
        linear = self.flat.split("## Linear", 1)[1].split(" ## ", 1)[0]
        self.assertIn("Skip this section when `LEGWORK_LINEAR_TEAM` is unset", linear)

    def test_finds_panes_by_the_fields_herdr_prints(self):
        # herdr 0.9 prints the title as terminal_title_stripped.
        self.assertIn("terminal_title_stripped", self.body)
        self.assertIn("agent_status", self.body)

    def section(self, heading):
        parts = self.flat.split("## " + heading, 1)
        self.assertEqual(len(parts), 2, heading)
        return parts[1].split(" ## ", 1)[0]

    def test_names_itself_with_agent_rename(self):
        # `herdr pane rename` sets only the pane label, which `herdr agent
        # list` never prints; the watcher and step 2 read the agent name.
        self.assertNotIn("herdr pane rename", self.body)
        self.assertIn("herdr agent rename <your pane_id> switchboard",
                      self.section("Your pane's name"))

    def test_commands_run_through_the_bash_tool(self):
        # The quoting and the grep/tail lookups are POSIX shell; PowerShell
        # cannot parse '\'' and has no grep.
        self.assertIn("through the Bash tool (Git Bash on Windows), never PowerShell",
                      self.flat)

    def test_add_run_only_for_a_new_run(self):
        # Pane state alone cannot show a trailing CYCLE or DONE is stale: an
        # orchestrator still finishing its turn after writing one is working.
        self.assertNotIn("while its pane is working", self.flat)
        self.assertNotRegex(self.section("Watching"), r"--run`? when")
        self.assertIn("Never pass `--run` when you attach to a run already going",
                      self.flat)

    def test_run_checks_for_any_existing_orchestrator_first(self):
        part = self.section("Opening a pane")
        self.assertNotIn("no idle `orch-<project>` pane", part)
        for state in ("working", "blocked", "idle"):
            self.assertIn(state, part)
        self.assertIn("only when no `orch-<project>` agent exists", part)


class RoadmapPlanningSkillTests(unittest.TestCase):
    def setUp(self):
        self.body = text("roadmap-planning")
        self.flat = flat(self.body)

    def test_no_reviewers_rule(self):
        self.assertNotRegex(self.flat, re.compile(r"reviewers", re.I))

    def test_linear_sweep_reads_the_settings_and_is_skipped_when_unset(self):
        for key in ("LEGWORK_LINEAR_TEAM", "LEGWORK_LINEAR_TEAM_KEY",
                    "LEGWORK_LINEAR_SWEEP_LABELS"):
            self.assertIn(key, self.body)
        self.assertIn("Skip the sweep when `LEGWORK_LINEAR_TEAM` is unset", self.flat)

    def test_merge_rule_and_shared_repos_follow_own_owners(self):
        self.assertIn("LEGWORK_OWN_OWNERS", self.body)
        self.assertIn("any owner not in `LEGWORK_OWN_OWNERS`", self.flat)

    def test_follows_the_house_rules_for_its_commits_and_pr(self):
        # It commits the legwork repo and opens a docs PR, so the user's
        # authorship and attribution rules apply, as in /ship and /fanout.
        self.assertIn("`{{LEGWORK_DIR}}/house-rules.md`", self.body)
        self.assertIn("house-rules.md` applies to each commit, pull request", self.flat)

    def test_keeps_the_tracker_skill_and_the_live_machine_rule(self):
        self.assertIn("legwork-tracker", self.body)
        self.assertIn("orchestrator-only", self.body)


if __name__ == "__main__":
    unittest.main()
