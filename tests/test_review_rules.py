"""Pins the pull request review rule. The user writes who reviews, per repo
or per owner, in the `Pull request reviews` section of their house rules.
Every place that opens a pull request or ends a review loop follows that
section, by the route it already takes to the house rules. With nothing
written for a repo, nobody is requested, and the PR's author never is.

The text is read with its line wraps folded, so a rewrap does not break a
test. The example section is checked for placeholders only, so no real
owner, repo or login is ever shipped as a sample.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ORCH = REPO / "orchestration"
EXAMPLE = REPO / "house-rules.example.md"

# Each place, with the way it already reaches the house rules.
PLACES = {
    "ship": (ORCH / "commands" / "ship.md", "`house-rules.md`"),
    "bugbot": (ORCH / "commands" / "bugbot.md", "`house-rules.md`"),
    "fanout": (ORCH / "skills" / "fanout" / "SKILL.md", "`<legwork>/house-rules.md`"),
    "switchboard-protocol": (ORCH / "skills" / "switchboard-protocol" / "SKILL.md",
                             "`<legwork>/house-rules.md`"),
    "roadmap-planning": (ORCH / "skills" / "roadmap-planning" / "SKILL.md",
                         "`{{LEGWORK_DIR}}/house-rules.md`"),
}

DEFAULT = "request nobody and leave reviews to the user"
AUTHOR = "never the PR's author"


def flat(path):
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def section(body, heading):
    """The folded text under a `## <heading>` line, up to the next `## `."""
    start = body.index(f"## {heading}")
    end = body.find(" ## ", start + 3)
    return body[start:end if end != -1 else len(body)]


def item(body, start, stop):
    """The folded text from `start` up to the next `stop` after it, or to
    the end: one step or list item."""
    at = body.index(start)
    end = body.find(stop, at + len(start))
    return body[at:end if end != -1 else len(body)]


class EveryPlaceTests(unittest.TestCase):
    def test_reads_the_section_by_its_house_rules_route(self):
        for name, (path, route) in PLACES.items():
            body = flat(path)
            self.assertIn(route, body, name)
            self.assertIn("`Pull request reviews`", body, name)

    def test_nothing_written_for_a_repo_means_nobody_is_requested(self):
        for name, (path, _) in PLACES.items():
            self.assertIn(DEFAULT, flat(path), name)

    def test_the_author_is_never_requested(self):
        for name, (path, _) in PLACES.items():
            self.assertIn(AUTHOR, flat(path), name)

    def test_no_blanket_reviewer_rule(self):
        # The rule is the user's, per repo; no shipped file decides it.
        for name, (path, _) in PLACES.items():
            body = flat(path)
            self.assertNotIn("no reviewers", body.lower(), name)
            self.assertNotIn("--reviewer ", body, name)


class ShipTests(unittest.TestCase):
    def setUp(self):
        self.body = flat(PLACES["ship"][0])

    def test_requests_the_open_time_reviewers_after_creating_the_pr(self):
        step = item(self.body, "5. Pull request.", " 6. Run /bugbot")
        self.assertIn("when the PR opens", step)
        self.assertIn("`gh pr edit <n> --add-reviewer <logins>`", step)
        self.assertLess(step.index("`gh pr create`"), step.index("--add-reviewer"))

    def test_leaves_the_after_the_bot_reviewers_to_bugbot(self):
        step = item(self.body, "5. Pull request.", " 6. Run /bugbot")
        self.assertIn("after the review bot is clear wait for step 6", step)

    def test_reply_names_the_reviewers(self):
        self.assertIn("reviewers requested", self.body[self.body.index("8. Reply"):])

    def test_passes_a_hold_on_to_bugbot(self):
        self.assertIn("`--hold-reviewers`", self.body[:self.body.index("Steps:")])
        step = item(self.body, "6. Run /bugbot", " 7. Run /wrap")
        self.assertIn("with `--hold-reviewers` if this run was given it", step)


class BugbotTests(unittest.TestCase):
    def setUp(self):
        self.body = flat(PLACES["bugbot"][0])

    def test_clear_requests_the_after_the_bot_reviewers(self):
        clear = item(self.body, "- `CLEAR`:", " - `TIMEOUT`:")
        self.assertIn("after the review bot is clear", clear)
        self.assertIn("`gh pr edit <pr> --add-reviewer <logins>`", clear)

    def test_no_bugbot_requests_them_once_the_pr_is_otherwise_ready(self):
        none = item(self.body, "- `NO_BUGBOT`:", " - `FINDINGS`:")
        self.assertIn("no bot to wait for", none)
        self.assertIn("once the PR is otherwise ready", none)
        self.assertIn("as for `CLEAR`", none)

    def test_a_caller_can_hold_the_after_the_bot_reviewers(self):
        # A caller whose own gate has not passed (fanout with a failed gate
        # line, an orchestrator before its adversarial review) holds them.
        head = self.body[:self.body.index("Steps, per round")]
        self.assertIn("`--hold-reviewers`", head)
        self.assertIn("With `--hold-reviewers`, request none of the "
                      "after-the-bot reviewers", head)
        for verdict, stop in (("- `CLEAR`:", " - `TIMEOUT`:"),
                              ("- `NO_BUGBOT`:", " - `FINDINGS`:")):
            self.assertIn("unless held", item(self.body, verdict, stop), verdict)

    def test_an_unclear_loop_requests_nobody(self):
        self.assertIn("Findings still open or a `TIMEOUT`: request none of the "
                      "after-the-bot reviewers", self.body)

    def test_reply_names_the_reviewers(self):
        self.assertIn("reviewers requested", self.body[self.body.index("After the loop"):])


HOUSE_RULES_ONLY = "except those in its `Pull request reviews` section"


class ReviewItemsAreNotHouseRulesTests(unittest.TestCase):
    """The review items are list items too, but they are not rules for
    every repo: no place pastes them into a brief or applies them to every
    step."""

    def test_every_reader_of_the_rules_leaves_the_section_out(self):
        for name in ("ship", "bugbot", "fanout", "roadmap-planning"):
            body = flat(PLACES[name][0])
            self.assertIn(HOUSE_RULES_ONLY, body, name)

    def test_the_brief_rules_leave_the_section_out(self):
        body = flat(PLACES["fanout"][0])
        rules = item(body, "Rules: house rules", " Finish:")
        self.assertIn("<every list item of house-rules.md, verbatim, "
                      "except those in its Pull request reviews section>", rules)
        step = item(body, "4. House rules:", " 5. Write")
        self.assertIn("Every list item in it, " + HOUSE_RULES_ONLY
                      + ", goes into every brief below, verbatim.", step)

    def test_the_example_says_the_section_is_not_pasted(self):
        body = flat(EXAMPLE)
        intro = body[:body.index("## Pull request reviews")]
        self.assertIn("Only the list items above the `Pull request reviews` "
                      "section are rules", intro)
        self.assertIn("are not rules and are not pasted into briefs", intro)

    def test_the_example_rules_say_nothing_about_reviewers(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        rules = text[:text.index("\n## Pull request reviews")]
        items = [line for line in rules.splitlines() if line.startswith("- ")]
        self.assertTrue(items)
        for line in items:
            self.assertNotIn("review", line.lower(), line)


class MergeWaitsForTheReviewersTests(unittest.TestCase):
    """Reviewers named for a repo are asked to review before a merge, not
    after one: a merge waits for their approval."""

    def test_fanout_does_not_merge_before_an_approval(self):
        body = flat(PLACES["fanout"][0])
        step = item(body, "2. When CLEAR and `--merge` was given:", " 3. Without `--merge`:")
        self.assertIn("If this repo's review item names any reviewers, merge only once "
                      "`gh pr view <n> --json reviewDecision` says `APPROVED`", step)
        self.assertIn("leave the PR open for them and the user", step)

    def test_the_protocol_merge_authorisation_waits_too(self):
        body = flat(PLACES["switchboard-protocol"][0])
        bullet = item(body, "- If the start prompt says you may merge", " - After each merge")
        self.assertIn("names any reviewers", bullet)
        self.assertIn("`APPROVED`", bullet)

    def test_roadmap_planning_says_the_merge_waits(self):
        body = flat(PLACES["roadmap-planning"][0])
        self.assertIn("approved by them", item(body, "2. First time", " 3. Grill"))
        step = item(body, "5. Tracker:", " 6. Tell the user")
        self.assertIn("their PRs wait for an approval before a merge", step)


class RoadmapDocPrTests(unittest.TestCase):
    def setUp(self):
        self.body = flat(PLACES["roadmap-planning"][0])

    def test_the_doc_pr_runs_bugbot(self):
        step = item(self.body, "4. Output:", " 5. Tracker:")
        self.assertIn("run `/bugbot` on it once it is open", step)

    def test_no_bugbot_repo_requests_them_once_ready(self):
        para = item(self.body, "Its `Pull request reviews` section", "## Steps")
        self.assertIn("once the PR is otherwise ready", para)


class FanoutLandTests(unittest.TestCase):
    def test_a_failed_gate_holds_the_after_the_bot_reviewers(self):
        body = flat(PLACES["fanout"][0])
        step = item(body, "1. `/bugbot <n>`", " 2. When CLEAR")
        self.assertIn("`/bugbot <n> --hold-reviewers` when the gate line said FAIL", step)


class ProtocolHoldTests(unittest.TestCase):
    def test_a_no_bugbot_repo_holds_them_until_step_3(self):
        body = flat(PLACES["switchboard-protocol"][0])
        bullet = item(body, "- Pull request reviews:", " - The switchboard file")
        self.assertIn("run `/ship` or `/bugbot` there with `--hold-reviewers` until "
                      "then", bullet)


class ExampleTests(unittest.TestCase):
    PLACEHOLDER = re.compile(r"your-[a-z-]+(?:/your-[a-z-]+)?")

    def setUp(self):
        self.body = EXAMPLE.read_text(encoding="utf-8")
        start = self.body.index("\n## Pull request reviews")
        end = self.body.find("\n## ", start + 1)
        self.section = self.body[start:end if end != -1 else len(self.body)]
        self.items = [line for line in self.section.splitlines()
                      if line.startswith("- ")]

    def test_section_has_a_repo_item_and_an_owner_item(self):
        firsts = [re.match(r"- `([^`]+)`", line).group(1) for line in self.items]
        self.assertTrue(any("/" in name for name in firsts), firsts)
        self.assertTrue(any("/" not in name for name in firsts), firsts)

    def test_items_use_placeholders_only(self):
        self.assertTrue(self.items)
        for line in self.items:
            for name in re.findall(r"`([^`]+)`", line):
                self.assertRegex(name, "^" + self.PLACEHOLDER.pattern + "$", line)
            for name in re.findall(r"[\w.-]+/[\w.-]+", line):
                self.assertRegex(name, "^" + self.PLACEHOLDER.pattern + "$", line)
            self.assertNotIn("@", line)

    # Every word the section may use that is not a placeholder. A bare
    # name (a login, an org) is not in it, so it fails until it is
    # replaced by a `your-...` placeholder. A new plain word goes here.
    VOCABULARY = set("""
        a after agents.md an and ask asked author beats below bot bugbot
        clear contributing current delete each end even every example
        fanout for github head if is it item items its leave list logins
        loop means name names never no nobody of on once one open opens
        or orchestrators org otherwise out own owner owner's per pr pr's
        pull read ready replace repo repo's request requested review
        reviewers reviews roadmap-planning says section ship skill team
        that the them they this to two under user want when whenever who
        with workers you your
    """.split())

    def test_every_word_is_plain_or_a_placeholder(self):
        text = self.section.replace("`", " ")
        for word in re.findall(r"[A-Za-z][\w'-]*(?:\.md)?", text):
            if re.fullmatch(self.PLACEHOLDER.pattern, word):
                continue
            self.assertIn(word.lower(), self.VOCABULARY, word)

    def test_items_show_both_times_and_both_kinds_of_who(self):
        items = re.sub(r"\s+", " ", "\n".join(self.items))
        self.assertIn("when the PR opens", items)
        self.assertIn("after the review bot is clear on the current head", items)
        self.assertIn("the reviewers the repo's AGENTS.md or CONTRIBUTING names", items)

    def test_section_states_the_default_the_author_and_the_order(self):
        prose = re.sub(r"\s+", " ", self.section)
        self.assertIn("request nobody and leave reviews to you", prose)
        self.assertIn("The PR's author is never requested", prose)
        self.assertIn("A repo's item beats its owner's", prose)

    def test_section_is_generic(self):
        work = re.compile(r"\badam\b|/Users/|/home/[a-z]|C:\\Users|"
                          r"~/\.claude-[a-z]|\b[A-Z]{2,5}-[0-9]+\b", re.I)
        for m in work.finditer(self.section):
            self.fail(f"leak: {m.group(0)!r}")
        self.assertNotIn(chr(0x2014), self.section)


class OnboardTests(unittest.TestCase):
    def test_house_rules_step_mentions_the_section(self):
        body = flat(REPO / "core" / "skills" / "legwork-onboard" / "SKILL.md")
        step = item(body, "11. **House rules.**", "## 3.")
        self.assertIn("`## Pull request reviews` section", step)

    def test_install_step_writes_the_section(self):
        body = flat(REPO / "core" / "skills" / "legwork-onboard" / "SKILL.md")
        step = item(body, "1. House rules, if they gave", " 2. Settings:")
        self.assertIn("if they gave any rules or review answers", step)
        self.assertIn("under a `## Pull request reviews` heading after the rules", step)


if __name__ == "__main__":
    unittest.main()
