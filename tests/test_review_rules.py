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


class BugbotTests(unittest.TestCase):
    def setUp(self):
        self.body = flat(PLACES["bugbot"][0])

    def test_clear_requests_the_after_the_bot_reviewers(self):
        clear = item(self.body, "- `CLEAR`:", " - `TIMEOUT`:")
        self.assertIn("after the review bot is clear", clear)
        self.assertIn("`gh pr edit <pr> --add-reviewer <logins>`", clear)

    def test_no_bugbot_requests_them_at_once(self):
        none = item(self.body, "- `NO_BUGBOT`:", " - `FINDINGS`:")
        self.assertIn("no bot to wait for", none)
        self.assertIn("as for `CLEAR`", none)

    def test_an_unclear_loop_requests_nobody(self):
        self.assertIn("Findings still open or a `TIMEOUT`: request none of the "
                      "after-the-bot reviewers", self.body)

    def test_reply_names_the_reviewers(self):
        self.assertIn("reviewers requested", self.body[self.body.index("After the loop"):])


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


if __name__ == "__main__":
    unittest.main()
