"""Legwork is only the tracker: projects, prompts and the commands that work
them. The orchestrator layer lives in Orchi, which works from a legwork
tracker; legwork never depends on it.

These tests pin that from legwork's side. The docs, the example settings and
the commands describe the tracker alone, and the README says where the
orchestrator went. House rules are legwork's own: the tracker skill follows
them, so a tool such as Orchi can extend /wrap with one line there and
legwork needs no code of its own for it.
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ORCHI = "https://github.com/adamentwistle/orchi-build"

# What the docs a user reads first, and the plugin itself, must not name.
GONE = re.compile(
    r"orchestration/|switchboard|fanout|/ship\b|/bugbot\b|bugbot-wait|"
    r"usage-guard|pane-notify|roadmap-planning|codex-agent|"
    r"LEGWORK_(ORCHESTRATION|SWITCHBOARD|SESSION_ARGS|CONTEXT_WINDOW|"
    r"OWN_OWNERS|BUGBOT_OWNERS|LINEAR_|PR_SKILL|UI_SKILL|USAGE_CACHE)",
    re.I)

# A path copied in from someone's machine.
LEAKS = re.compile(r"/Users/|/home/[a-z]|C:\\{1,2}Users")


# The one file that names what moved, so a user can remove it.
MOVED_NOTE = Path("docs") / "orchestrator-moved.md"
TEXT = {".py", ".md", ".sh", ".js", ".json", ".yml", ".yaml", ".plist",
        ".tape", ".txt", ".toml", ""}


def shipped_files():
    """Every file in the repo but the tests and the note, as git tracks it,
    or as found on disk outside a checkout. Caches are never shipped."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.split()
        paths = [REPO / p for p in out]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in REPO.rglob("*") if ".git" not in p.parts]
    return [p for p in paths if p.is_file()
            and p.relative_to(REPO).parts[0] != "tests"
            and p.relative_to(REPO) != MOVED_NOTE
            and "__pycache__" not in p.parts and p.suffix in TEXT]


def names_gone(test, paths):
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for m in GONE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            test.fail(f"{path.relative_to(REPO)}:{line} names {m.group(0)!r}")


def user_docs():
    files = [REPO / n for n in ("README.md", "ARCHITECTURE.md", "SETUP.md",
                                "CONFIG.md", "CONTRIBUTING.md", "SECURITY.md",
                                "config.example", "house-rules.example.md",
                                "examples/README.md")]
    files += sorted((REPO / "core").rglob("*.md"))
    return files


class TrackerDocsTests(unittest.TestCase):
    def test_docs_and_plugin_describe_only_the_tracker(self):
        names_gone(self, user_docs())

    def test_readme_points_at_orchi(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertTrue(ORCHI in text, "README.md has no link to Orchi")


class NoOrchestrationTests(unittest.TestCase):
    def test_the_orchestration_layer_is_gone(self):
        # A checkout that ran the old suite keeps an ignored __pycache__
        # there after the pull; only real files count.
        left = [p for p in (REPO / "orchestration").rglob("*")
                if p.is_file() and "__pycache__" not in p.parts]
        self.assertFalse(left, f"orchestration/ still holds {left[:3]}")

    def test_nothing_shipped_names_the_orchestrator(self):
        names_gone(self, shipped_files())


class MovedNoteTests(unittest.TestCase):
    """An install from an older checkout left commands, skills, hooks and a
    scheduled job that call scripts this checkout no longer has. One note
    says what they are and how to remove each."""

    NOTE = REPO / "docs" / "orchestrator-moved.md"

    def test_readme_links_the_note(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertTrue("docs/orchestrator-moved.md" in text, "README.md has no link to the note")

    def test_note_says_how_to_remove_each_piece(self):
        self.assertTrue(self.NOTE.is_file(), "docs/orchestrator-moved.md is missing")
        text = self.NOTE.read_text(encoding="utf-8")
        for piece in ("commands/ship.md", "commands/bugbot.md", "commands/fanout.md",
                      "skills/fanout", "skills/switchboard-protocol", "skills/switchboard",
                      "skills/roadmap-planning", "agents/codex.md",
                      "orchestration/bin/usage-guard", "orchestration/bin/pane-notify",
                      "launchctl bootout gui/$(id -u)/dev.legwork.switchboard-health",
                      "schtasks /Delete /TN dev.legwork.switchboard-health /F",
                      ".claude/settings.local.json"):
            self.assertIn(piece, text)


class OnboardTests(unittest.TestCase):
    def test_onboard_runs_python_through_the_detected_interpreter(self):
        text = (REPO / "core" / "skills" / "legwork-onboard" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("`<python>` below", text)
        for m in re.finditer(r"\bpython3? (suite|scripts|core)/", text):
            self.fail(f"bare call in the onboard skill: {m.group(0)!r}")

    def test_leak_pattern_catches_each_platform(self):
        for sample in ("/Users/alice/legwork", "/home/alice/legwork",
                       "C:\\Users\\alice\\legwork"):
            self.assertTrue(LEAKS.search(sample), sample)

    def test_no_machine_paths(self):
        for path in user_docs():
            for m in LEAKS.finditer(path.read_text(encoding="utf-8")):
                self.fail(f"{path.relative_to(REPO)}: {m.group(0)!r}")


class OnboardConfigTests(unittest.TestCase):
    def test_settings_step_writes_the_legwork_dir(self):
        # A config copied from config.example would carry its
        # LEGWORK_DIR=$HOME/legwork, and the wizard's --yes run keeps it, so
        # a checkout anywhere else would get its timer and hooks pointed at
        # ~/legwork. The step sets LEGWORK_DIR itself and copies nothing.
        text = (REPO / "core" / "skills" / "legwork-onboard" / "SKILL.md").read_text(
            encoding="utf-8")
        step = text[text.index("2. Settings"):text.index("3. The queue")]
        self.assertIn("`LEGWORK_DIR`", step)
        self.assertNotIn("config.example", step)


class HouseRulesTests(unittest.TestCase):
    def test_tracker_skill_follows_the_house_rules(self):
        text = (REPO / "core" / "skills" / "legwork-tracker" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertTrue("`house-rules.md`" in text,
                        "the legwork-tracker skill never names house-rules.md")

    def test_example_has_no_review_section(self):
        text = (REPO / "house-rules.example.md").read_text(encoding="utf-8")
        self.assertFalse("## Pull request reviews" in text,
                         "house-rules.example.md still has a review section")


class ReviewFindingTests(unittest.TestCase):
    def read(self, *parts):
        return (REPO.joinpath(*parts)).read_text(encoding="utf-8")

    def test_onboard_reads_the_doctor_verdict_not_its_exit_status(self):
        # --doctor prints its verdict and exits 0 either way.
        text = self.read("core", "skills", "legwork-onboard", "SKILL.md")
        step = text[text.index("## 5."):text.index("## Rules")]
        self.assertNotIn("must exit 0", step)
        self.assertIn("Doctor: all green", step)

    def test_every_command_that_writes_follows_the_house_rules(self):
        # A session runs the command's own steps, so a rule that says what
        # to run after /wrap only works when the command says to follow it.
        for verb in ("add", "log", "shelve", "wrap"):
            text = self.read("core", "commands", f"{verb}.md")
            self.assertTrue("`house-rules.md`" in text, f"/{verb} never names house-rules.md")
            self.assertTrue(f"after /{verb}" in text, f"/{verb} has no after-step")

    def test_the_after_step_runs_before_the_commit(self):
        # Whatever a house-rule step writes must be in the commit and push.
        text = self.read("core", "skills", "legwork-tracker", "SKILL.md")
        after = text.index("house rule says to do after this verb")
        self.assertLess(after, text.index("Rebuild the dashboard from the legwork repo"))
        self.assertLess(after, text.index("Commit your changes with an honest message"))

    def test_switching_review_mode_clears_the_old_mode(self):
        # The wizard picks n8n whenever LEGWORK_WEBHOOK_URL is set, so a
        # re-run that moves to the local reviewer must take that line out.
        text = self.read("core", "skills", "legwork-onboard", "SKILL.md")
        step = text[text.index("2. Settings"):text.index("3. The queue")]
        self.assertIn("comment out the review keys of the modes they did not pick", step)

    def test_an_older_review_section_is_not_a_rule(self):
        # An earlier /onboard wrote a `## Pull request reviews` section into
        # house-rules.md, whose items say who reviews pull requests.
        text = self.read("core", "skills", "legwork-tracker", "SKILL.md")
        rules = text[text.index("## House rules"):text.index("## File format")]
        self.assertIn("`## Pull request reviews`", rules)


if __name__ == "__main__":
    unittest.main()
