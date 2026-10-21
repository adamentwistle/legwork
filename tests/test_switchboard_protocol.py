"""Pins the rules in the switchboard-protocol skill that orchestrators and the
switchboard both rely on: how ids are numbered, where `Replaces` goes, how a
pane answer and a grant are handled, the run-started UPDATE, and branch
cleanup after a merge. The text is read with its line wraps folded, so a
rewrap does not break a test."""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "orchestration" / "skills" / "switchboard-protocol" / "SKILL.md"


def folded():
    return re.sub(r"\s+", " ", SKILL.read_text(encoding="utf-8"))


def section(text, heading):
    start = text.index(f"## {heading}")
    end = text.find(" ## ", start + 3)
    return text[start:end if end != -1 else len(text)]


class ProtocolRulesTests(unittest.TestCase):
    def setUp(self):
        self.text = folded()

    def test_ids_come_from_the_file_never_from_memory(self):
        entry = section(self.text, "Entry format")
        self.assertIn("Take `<n>` from the file each time you write", entry)
        self.assertIn("never from memory", entry)
        self.assertIn("`<project>-g<N>`", entry)

    def test_replaces_goes_on_the_line_after_the_heading(self):
        self.assertIn("carries `Replaces <id>` on the line after its heading",
                      self.text)
        self.assertNotIn("`Replaces <id>` on its first line", self.text)

    def test_pane_answer_needs_a_decided_that_replaces(self):
        self.assertIn("write a `DECIDED` entry with `Replaces <id>` on the line "
                      "after its heading", self.text)
        self.assertIn("Without that line the `DECISION` stays open", self.text)

    def test_manual_mode_still_writes_answered_itself(self):
        self.assertIn("The one exception is `LEGWORK_SWITCHBOARD=manual`", self.text)
        self.assertIn("`ANSWERED <id>: <their words verbatim>", self.text)

    def test_grant_prompt_is_noted_not_answered(self):
        self.assertIn("A `SWITCHBOARD GRANT <id>: ...` prompt is a delegation",
                      self.text)
        self.assertIn("already recorded in the switchboard file; note it and "
                      "carry on", self.text)

    def test_run_started_update_comes_first(self):
        setup = section(self.text, "Setup, once per session")
        self.assertIn("Before any other work, write an `UPDATE` that says the run "
                      "started", setup)

    def test_merged_branches_are_deleted_and_checked(self):
        setup = section(self.text, "Setup, once per session")
        order = ["remove the worktree", "`git push origin --delete <branch>`",
                 "`git ls-remote --heads origin <branch>` prints nothing",
                 "`git branch -D <branch>`"]
        spots = [setup.index(step) for step in order]
        self.assertEqual(spots, sorted(spots))
        self.assertIn("A branch you could not delete goes in your `DONE`", setup)
        self.assertIn("left for the user", setup)

    def test_undeleted_branch_is_the_one_hands_job_reported_in_done(self):
        escalate = section(self.text, "Decide or escalate")
        self.assertIn("The one exception is a branch you could not delete "
                      "after a merge", escalate)
        self.assertIn("is not a `DECISION`", escalate)

    def test_switchboard_is_claude_or_manual(self):
        self.assertIn("`LEGWORK_SWITCHBOARD`: `claude`", self.text)

    def test_reviews_follow_the_house_rules_section(self):
        # Was a check that no reviewer rule exists. The rule is now the
        # user's, per repo, in the house rules' Pull request reviews section,
        # which this skill reaches through <legwork> like its settings.
        setup = section(self.text, "Setup, once per session")
        self.assertIn("`<legwork>/house-rules.md`", setup)
        self.assertIn("`Pull request reviews` section", setup)
        self.assertIn("once step 3 of the quality gate passes", setup)
        self.assertIn("do not escalate it", setup)
        self.assertNotIn("no reviewers", self.text.lower())

    def test_no_names(self):
        self.assertNotIn("Adam", self.text)
        self.assertNotIn("—", self.text)


if __name__ == "__main__":
    unittest.main()
