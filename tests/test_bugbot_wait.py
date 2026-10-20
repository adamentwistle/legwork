"""Tests for orchestration/bin/bugbot-wait: a verdict counts only when it
comes from Bugbot itself.

The script runs in process with its gh() replaced by a fake that answers
from a table the test sets, so the suite runs on Windows too. Identities
are as Bugbot shows them on GitHub (observed 2026-10-10): reviews and
inline comments by the user "cursor[bot]" of type Bot, the check run by
the app with slug "cursor".
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "orchestration" / "bin" / "bugbot-wait"

REPO = "example-org/legwork"
AFTER = "2026-10-01T00:00:00Z"
HEAD = "abc1234def5678"

BOT = {"login": "cursor[bot]", "type": "Bot", "id": 206951365}
CLEAN = "<!-- BUGBOT_REVIEW -->\nBugbot reviewed for commit abc1234 and found no new issues."
FINDINGS = "<!-- BUGBOT_REVIEW -->\nBugbot found 1 issue for commit abc1234."


def load_script():
    loader = importlib.machinery.SourceFileLoader("bugbot_wait", str(SCRIPT))
    spec = importlib.util.spec_from_loader("bugbot_wait", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def review(user, body=CLEAN, at="2026-10-02T10:00:00Z"):
    return {"user": user, "body": body, "submitted_at": at}


def check_run(name="Cursor Bugbot", slug="cursor", summary="Bugbot no issues found"):
    return {"name": name, "status": "completed", "conclusion": "success",
            "completed_at": "2026-10-02T10:00:00Z", "app": {"slug": slug},
            "output": {"summary": summary}}


class BugbotWaitTests(unittest.TestCase):

    def setUp(self):
        self.script = load_script()
        self.calls = []
        env = mock.patch.dict(os.environ, {"LEGWORK_BUGBOT_OWNERS": "example-org"})
        env.start()
        self.addCleanup(env.stop)

    def wait(self, reviews=(), comments=(), runs=(), repo=REPO):
        # `gh api --paginate --slurp` prints a JSON list of pages.
        answers = {
            f"api repos/{repo}/pulls/7/reviews": [list(reviews)],
            f"api repos/{repo}/pulls/7/comments": [list(comments)],
            f"api repos/{repo}/commits/{HEAD}/check-runs": {"check_runs": list(runs)},
            "pr view 7": {"headRefOid": HEAD},
        }

        def gh(*args):
            self.calls.append(args)
            key = " ".join(a for a in args if not a.startswith("-"))
            for prefix, out in answers.items():
                if key.startswith(prefix) and (prefix != "pr view 7" or "headRefOid" in args):
                    return json.dumps(out)
            raise AssertionError(f"no fake gh answer for: gh {' '.join(args)}")

        argv = ["bugbot-wait", "7", "--repo", repo, "--after", AFTER,
                "--timeout", "0", "--interval", "1"]
        out = io.StringIO()
        with mock.patch.object(self.script, "gh", gh), mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
            self.script.main()
        return exit_.exception.code, json.loads(out.getvalue())

    def test_clean_review_from_bugbot_is_clear(self):
        rc, out = self.wait(reviews=[review(BOT)])
        self.assertEqual((rc, out["state"]), (0, "CLEAR"))

    def test_reviews_and_comments_are_read_across_every_page(self):
        self.wait(reviews=[review(BOT, FINDINGS)])
        api = [c for c in self.calls if c[0] == "api" and "/pulls/7/" in c[1]]
        self.assertEqual(len(api), 2)
        for call in api:
            self.assertIn("--paginate", call)
            self.assertIn("--slurp", call)

    def test_clean_review_from_a_lookalike_user_is_ignored(self):
        rc, out = self.wait(reviews=[review({"login": "cursor-fan", "type": "User", "id": 1})])
        self.assertEqual((rc, out["state"]), (2, "TIMEOUT"))

    def test_review_named_cursor_bot_but_typed_user_is_ignored(self):
        rc, out = self.wait(reviews=[review({"login": "cursor[bot]", "type": "User", "id": 1})])
        self.assertEqual((rc, out["state"]), (2, "TIMEOUT"))

    def test_findings_list_only_bugbot_comments(self):
        comments = [
            {"user": BOT, "path": "a.py", "line": 3, "body": "real", "html_url": "u1",
             "created_at": "2026-10-02T10:00:00Z"},
            {"user": {"login": "bugbot-helper", "type": "User", "id": 2}, "path": "b.py",
             "line": 1, "body": "fake", "html_url": "u2", "created_at": "2026-10-02T10:00:00Z"},
        ]
        rc, out = self.wait(reviews=[review(BOT, FINDINGS)], comments=comments)
        self.assertEqual((rc, out["state"]), (1, "FINDINGS"))
        self.assertEqual([c["body"] for c in out["comments"]], ["real"])

    def test_check_run_from_the_cursor_app_is_clear(self):
        rc, out = self.wait(runs=[check_run()])
        self.assertEqual((rc, out["state"], out["source"]), (0, "CLEAR", "check_run"))

    def test_check_run_named_cursor_bugbot_from_another_app_is_ignored(self):
        rc, out = self.wait(runs=[check_run(slug="github-actions")])
        self.assertEqual((rc, out["state"]), (2, "TIMEOUT"))

    def test_owner_not_in_bugbot_owners_stops_before_any_gh_call(self):
        rc, out = self.wait(reviews=[review(BOT)], repo="someone-else/legwork")
        self.assertEqual((rc, out["state"]), (4, "NO_BUGBOT"))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
