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


def user_docs():
    files = [REPO / n for n in ("README.md", "ARCHITECTURE.md", "SETUP.md",
                                "CONFIG.md", "CONTRIBUTING.md", "SECURITY.md",
                                "config.example", "house-rules.example.md",
                                "examples/README.md")]
    files += sorted((REPO / "core").rglob("*.md"))
    return files


class TrackerDocsTests(unittest.TestCase):
    def test_docs_and_plugin_describe_only_the_tracker(self):
        for path in user_docs():
            text = path.read_text(encoding="utf-8")
            for m in GONE.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                self.fail(f"{path.relative_to(REPO)}:{line} names {m.group(0)!r}")

    def test_readme_points_at_orchi(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertTrue(ORCHI in text, "README.md has no link to Orchi")


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


if __name__ == "__main__":
    unittest.main()
