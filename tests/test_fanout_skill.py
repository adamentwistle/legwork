"""Tests for the text of orchestration/skills/fanout/SKILL.md.

The skill is prose an orchestrator follows, so these check that the lessons
from earlier runs are in it (worker pane names, the follow-up wait, refused
pushes, cleanup before a merge, the cycle threshold), that what the public
copy already had is kept, and that nothing tied to one person's setup
leaks in.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "orchestration" / "skills" / "fanout" / "SKILL.md"


def text():
    return SKILL.read_text(encoding="utf-8")


def section(body, heading):
    """The text under a `## <heading>` line, up to the next `## `."""
    start = body.index(f"\n## {heading}")
    end = body.find("\n## ", start + 1)
    return body[start:end if end != -1 else len(body)]


class WorkerPaneTests(unittest.TestCase):
    def test_worker_panes_are_labelled_fw(self):
        body = text()
        self.assertIn("--label fw-<slug>", body)
        for verb in ("start", "prompt", "wait", "read", "send-keys"):
            self.assertIn(f"herdr agent {verb} fw-<slug>", body, verb)

    def test_no_worker_command_uses_the_bare_slug(self):
        for m in re.finditer(r"herdr agent \w[\w-]* <slug>", text()):
            self.fail(f"bare slug: {m.group(0)!r}")

    def test_pane_notify_stays_quiet_for_worker_panes(self):
        body = text()
        self.assertIn("pane-notify", body)
        self.assertRegex(body, r"name always starts\s+`fw-`")

    def test_oh_my_zsh_update_question_is_answered_before_start(self):
        body = text()
        self.assertIn("herdr pane read <pane_id> --lines 5", body)
        self.assertIn("herdr pane send-text <pane_id> n", body)
        self.assertLess(body.index("herdr pane send-text <pane_id> n"),
                        body.index("herdr agent start fw-<slug>"))


class WaitTests(unittest.TestCase):
    def test_follow_up_prompt_waits_for_working_first(self):
        wait = section(text(), "4. Wait")
        self.assertIn("herdr agent wait fw-<slug> --until working --timeout 30000", wait)
        self.assertRegex(wait, r"A `done` only counts when")

    def test_waits_still_fit_the_bash_limit(self):
        wait = section(text(), "4. Wait")
        self.assertIn("herdr agent wait fw-<slug> --timeout 540000", wait)
        self.assertNotIn("5400000", wait)

    def test_refused_push_is_blocked_not_pushed_for_the_worker(self):
        wait = section(text(), "4. Wait")
        self.assertIn("`git push` or `gh pr create` is refused", wait)
        self.assertIn("Never push it or open the PR from your own session", wait)
        self.assertIn("the user approves pushes", wait)


class LandTests(unittest.TestCase):
    def test_pane_and_worktree_go_before_the_merge(self):
        land = section(text(), "5. Land")
        self.assertIn("git ls-remote --heads origin <branch>", land)
        self.assertIn("git push origin --delete <branch>", land)
        self.assertIn("LEGWORK_OWN_OWNERS", land)


class OrchestratorTests(unittest.TestCase):
    def test_orchestrator_runs_on_opus_high(self):
        self.assertIn("Run the orchestrator itself on Opus at high effort", text())

    def test_machine_changes_belong_to_the_orchestrator(self):
        body = text()
        self.assertIn("Changes to the user's machine outside the repo", body)
        self.assertIn("scheduled task", body)


class CycleTests(unittest.TestCase):
    def test_cycle_threshold_and_switchboard_branch(self):
        budget = section(text(), "Context budget")
        self.assertIn("300,000 tokens or 60% of the window", budget)
        self.assertIn("each time a PR opens or lands", budget)
        self.assertIn("When reporting to a switchboard", budget)
        self.assertIn("Resume: /fanout <project> --resume", budget)
        self.assertIn("LEGWORK_SWITCHBOARD=manual", budget)

    def test_outside_herdr_still_prints_the_resume_line(self):
        budget = section(text(), "Context budget")
        self.assertIn("Outside Herdr, stop there and print `/fanout <project> --resume`",
                      budget)

    def test_no_run_story(self):
        self.assertNotIn("2026-10-12", text())


class KeptFromPublicTests(unittest.TestCase):
    def test_public_pieces_are_kept(self):
        body = text()
        for piece in ("`<legwork>` below is the legwork repo", "`<legwork>/config`",
                      "LEGWORK_OWN_OWNERS", "LEGWORK_BUGBOT_OWNERS", "LEGWORK_PR_SKILL",
                      "{{LEGWORK_BIN}}/bugbot-wait", "`herdr --skill`",
                      "<legwork>/house-rules.md", "every list item of house-rules.md, verbatim",
                      "in the\n  shape of the <LEGWORK_PR_SKILL> skill"):
            self.assertIn(piece, body)


class AskOnceTests(unittest.TestCase):
    """The run is unattended, so the two asks it makes never hold it up."""

    @staticmethod
    def paras(body):
        return [" ".join(p.split()) for p in body.split("\n\n")]

    def test_preamble_names_the_asks_and_never_waits(self):
        intro = " ".join(text().split("\n## ", 1)[0].split())
        self.assertIn("never wait for an answer", intro)

    def test_machine_change_ask_has_a_no_answer_fallback(self):
        para = next(p for p in self.paras(text()) if "Ask for that word" in p)
        self.assertIn("Do not wait for it", para)
        self.assertIn("wrap log", para)

    def test_merge_ask_has_a_no_answer_fallback(self):
        land = section(text(), "5. Land each PR")
        item = next(p for p in self.paras(land) if "Ask for their word" in p)
        self.assertIn("Do not wait for it", item)
        self.assertIn("leave the merge to the user, as without `--merge`", item)
        self.assertIn("wrap log", item)


class LeakTests(unittest.TestCase):
    # Generic shapes only, so no private name is written into a public test:
    # home folders, a second Claude config folder, tracker issue ids, and the
    # user's own name where the skill should say "the user".
    WORK = re.compile(r"\badam\b|/Users/|/home/[a-z]|C:\\Users|"
                      r"~/\.claude-[a-z]|\b[A-Z]{2,5}-[0-9]+\b", re.I)

    def test_no_work_names(self):
        for m in self.WORK.finditer(text()):
            self.fail(f"leak: {m.group(0)!r}")

    def test_no_em_dashes(self):
        self.assertNotIn(chr(0x2014), text())

    def test_no_reviewers_rule_left_out(self):
        body = text()
        self.assertNotIn("--reviewer", body)
        self.assertNotIn("no reviewers", body)

    def test_linear_is_not_in_the_skill(self):
        self.assertNotIn("Linear", text())


if __name__ == "__main__":
    unittest.main()
