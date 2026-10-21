"""Tests for orchestration/bin/switchboard-answer.

The script runs in process with its herdr() replaced by a fake that records
each call and answers with an exit code the test sets, so the suite runs on
Windows too. One test runs the real herdr() with an empty PATH, for a herdr
that is not installed. Switchboard entries come from the same sanitised runs
as the switchboard-wait fixtures.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "orchestration" / "bin" / "switchboard-answer"
WAIT = HERE.parent / "orchestration" / "bin" / "switchboard-wait"
FIXTURES = HERE / "fixtures" / "switchboard"


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def blocks(name):
    """Entry id -> its text, header to the next header."""
    text = (FIXTURES / name).read_text(encoding="utf-8")
    heads = list(re.finditer(r"^## (\S+) ", text, re.M))
    out = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out[m.group(1)] = text[m.start():end].rstrip("\n") + "\n\n"
    return out


BOARD = blocks("legwork-switchboard.md")
RECIPES = blocks("recipe-box-switchboard.md")
STAMP = re.compile(r"\(\d{4}-\d\d-\d\d \d\d:\d\d\)$")
NOT_FOUND = json.dumps({"error": {"code": "agent_not_found"}})


class AnswerCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "legwork"
        self.board = self.repo / ".legwork" / "switchboard.md"
        self.script = load(SCRIPT, "switchboard_answer")
        self.calls, self.code, self.err = [], 0, ""
        self.raises = None

        def herdr(*args):
            self.calls.append(list(args))
            if self.raises:
                raise self.raises
            return subprocess.CompletedProcess(["herdr", *args], self.code, "",
                                               NOT_FOUND if self.code else "")

        self.fake = mock.patch.object(self.script, "herdr", herdr)
        self.fake.start()
        self.addCleanup(self.fake.stop)

    def write(self, *texts):
        self.board.parent.mkdir(parents=True, exist_ok=True)
        self.board.write_bytes("".join(texts).encode("utf-8"))

    def text(self):
        return self.board.read_bytes().decode("utf-8")

    def run_main(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = self.script.main(argv)
        self.err = err.getvalue()
        return rc

    def answer(self, ident, words, pane="w1:p2"):
        return self.run_main([str(self.repo), pane, ident, words])

    def grant(self, words, scope, pane="w1:p2"):
        return self.run_main(["--grant", str(self.repo), pane, words, "--scope", scope])


class AnswerTests(AnswerCase):

    def test_answer_sends_and_appends(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"].rstrip("\n") + "\n")
        before = self.text()
        self.assertEqual(self.answer("legwork-151", "B"), 0)
        self.assertEqual(self.calls,
                         [["agent", "prompt", "w1:p2", "SWITCHBOARD ANSWER legwork-151: B"]])
        self.assertTrue(self.text().startswith(before))
        added = self.text()[len(before):]
        self.assertTrue(added.startswith("ANSWERED legwork-151: B ("), added)
        self.assertTrue(added.endswith(")\n"), added)
        self.assertRegex(added.strip(), STAMP)

    def test_answer_appends_on_own_line_when_file_has_no_final_newline(self):
        self.write(BOARD["legwork-151"].rstrip("\n"))
        self.assertEqual(self.answer("legwork-151", "A"), 0)
        self.assertIn("\nANSWERED legwork-151: A (", self.text())

    def test_answer_passes_quotes_and_colons_byte_for_byte(self):
        self.write(BOARD["legwork-151"])
        words = '''B: "no cache", it's 'fine' $HOME `x` %PATH% \\n  spaced  '''
        self.assertEqual(self.answer("legwork-151", words), 0)
        self.assertEqual(self.calls[0][3], f"SWITCHBOARD ANSWER legwork-151: {words}")
        self.assertIn(f"ANSWERED legwork-151: {words} (", self.text())

    def test_answer_with_newline_is_refused(self):
        for words in ("a\nb", "a\r\nb", "a\rb"):
            with self.subTest(words=words):
                self.write(BOARD["legwork-151"])
                before = self.text()
                self.assertEqual(self.answer("legwork-151", words), 2)
                self.assertEqual((self.calls, self.text()), ([], before))
                self.assertIn("newline", self.err)

    def test_answer_empty_is_refused(self):
        self.write(BOARD["legwork-151"])
        self.assertEqual(self.answer("legwork-151", "  "), 2)
        self.assertEqual(self.calls, [])

    def test_answer_unknown_id_is_refused(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        before = self.text()
        self.assertEqual(self.answer("legwork-999", "B"), 1)
        self.assertEqual((self.calls, self.text()), ([], before))
        self.assertIn("legwork-999", self.err)

    def test_answer_to_update_entry_is_refused(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        self.assertEqual(self.answer("legwork-150", "B"), 1)
        self.assertEqual(self.calls, [])

    def test_answer_already_answered_is_refused(self):
        self.write(BOARD["legwork-151"], "ANSWERED legwork-151: B (2026-09-25 20:10)\n")
        before = self.text()
        self.assertEqual(self.answer("legwork-151", "A"), 1)
        self.assertEqual((self.calls, self.text()), ([], before))

    def test_answer_replaced_decision_is_refused(self):
        self.write(RECIPES["recipe-box-17"], RECIPES["recipe-box-20"])
        self.assertIn("Replaces recipe-box-17", self.text())
        self.assertEqual(self.answer("recipe-box-17", "A"), 1)
        self.assertEqual(self.calls, [])

    def test_answer_when_herdr_fails_writes_nothing(self):
        self.write(BOARD["legwork-151"])
        before = self.text()
        self.code = 1
        self.assertEqual(self.answer("legwork-151", "B"), 1)
        self.assertEqual(self.text(), before)
        self.assertIn("agent_not_found", self.err)

    def test_answer_when_herdr_times_out_writes_nothing(self):
        self.write(BOARD["legwork-151"])
        before = self.text()
        self.raises = subprocess.TimeoutExpired(["herdr"], 30)
        self.assertEqual(self.answer("legwork-151", "B"), 1)
        self.assertEqual(self.text(), before)
        self.assertIn("timed out", self.err)

    def test_answer_when_herdr_missing_writes_nothing(self):
        self.write(BOARD["legwork-151"])
        before = self.text()
        self.fake.stop()  # the real herdr(), with nothing on PATH to run
        empty = self.repo / "empty-bin"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"PATH": str(empty)}):
            self.assertEqual(self.answer("legwork-151", "B"), 1)
        self.assertEqual(self.text(), before)
        self.assertIn("could not run herdr", self.err)

    def test_answer_missing_file_is_refused(self):
        self.assertEqual(self.answer("legwork-151", "B"), 1)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.board.exists())

    def test_answer_closes_the_decision_for_a_second_answer(self):
        self.write(BOARD["legwork-151"])
        self.assertEqual(self.answer("legwork-151", "B"), 0)
        self.assertEqual(self.answer("legwork-151", "A"), 1)
        self.assertEqual(len(self.calls), 1)

    def test_writes_keep_lf_line_endings(self):
        self.write(BOARD["legwork-151"])
        self.assertEqual(self.answer("legwork-151", "B"), 0)
        self.assertEqual(self.grant("go", "s"), 0)
        self.assertNotIn(b"\r", self.board.read_bytes())

    def test_bad_arguments(self):
        self.assertEqual(self.run_main([]), 2)
        self.assertEqual(self.run_main(["only", "three", "args"]), 2)
        self.assertEqual(self.run_main(["--grant", str(self.repo), "w1:p2", "words"]), 2)
        self.assertEqual(self.run_main(["--grant", str(self.repo), "w1:p2", "w", "--scop", "s"]),
                         2)
        self.assertEqual(self.calls, [])


class GrantTests(AnswerCase):

    def test_grant_appends_entry_and_sends(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        self.assertEqual(self.grant("answer the grill questions", "grill questions only"), 0)
        entry = self.text().split("\n## ")[-1]
        head, user, scope = entry.splitlines()[:3]
        self.assertRegex(head, r"^legwork-g1 GRANT \d{4}-\d\d-\d\d \d\d:\d\d$")
        self.assertEqual(user, 'User: "answer the grill questions"')
        self.assertEqual(scope, "Scope: grill questions only")
        self.assertTrue(self.text().endswith("\n"))
        self.assertEqual(self.calls, [["agent", "prompt", "w1:p2",
                                       "SWITCHBOARD GRANT legwork-g1: answer the grill "
                                       "questions (Scope: grill questions only)"]])

    def test_grant_number_is_one_more_than_existing_grants(self):
        self.write(BOARD["legwork-150"],
                   '## legwork-g1 GRANT 2026-09-26 10:00\nUser: "x"\nScope: y\n\n',
                   '## legwork-g2 GRANT 2026-09-26 11:00\nUser: "x"\nScope: y\n\n')
        self.assertEqual(self.grant("more", "scope"), 0)
        self.assertIn("## legwork-g3 GRANT ", self.text())
        self.assertIn("SWITCHBOARD GRANT legwork-g3:", self.calls[0][3])

    def test_grant_project_from_existing_entry_ids(self):
        self.write(RECIPES["recipe-box-17"])
        self.assertEqual(self.grant("go", "s"), 0)
        self.assertIn("## recipe-box-g1 GRANT ", self.text())

    def test_grant_project_falls_back_to_repo_dir_name(self):
        self.write("")
        self.assertEqual(self.grant("go", "s"), 0)
        self.assertIn("## legwork-g1 GRANT ", self.text())

    def test_grant_creates_missing_file(self):
        self.repo.mkdir()
        self.assertEqual(self.grant("go", "s"), 0)
        self.assertTrue(self.text().startswith("## legwork-g1 GRANT "))

    def test_grant_when_herdr_fails_writes_nothing(self):
        self.write(BOARD["legwork-151"])
        before = self.text()
        self.code = 1
        self.assertEqual(self.grant("go", "s"), 1)
        self.assertEqual(self.text(), before)

    def test_grant_refuses_newlines_and_empty(self):
        for words, scope in (("a\nb", "s"), ("a", "s\nt"), ("a", "s\rt"), ("", "s"), ("a", " ")):
            with self.subTest(words=words, scope=scope):
                self.write(BOARD["legwork-151"])
                before = self.text()
                self.assertEqual(self.grant(words, scope), 2)
                self.assertEqual((self.calls, self.text()), ([], before))

    def test_grant_words_keep_quotes(self):
        self.write(BOARD["legwork-151"])
        self.assertEqual(self.grant('say "yes" to it', "s"), 0)
        self.assertIn('User: "say "yes" to it"', self.text())


class WaitReadsWhatAnswerWritesTests(AnswerCase):
    """switchboard-wait still parses the file after an answer or a grant.
    The wait watches REPO:none, so it never calls herdr either."""

    def setUp(self):
        super().setUp()
        self.wait = load(WAIT, "switchboard_wait_for_answer")
        missing = self.repo.parent / "missing"
        for name, value in (("USAGE_CACHE", str(missing / "usage.json")), ("POLL", 0.2)):
            patch = mock.patch.object(self.wait, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.state = str(missing / "state.md")

    def run_wait(self, *flags):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.wait.main([*flags, "--state", self.state, f"{self.repo}:none"])
        return rc, out.getvalue().splitlines()

    def open_decisions(self, *flags):
        return [ln for ln in self.run_wait(*flags)[1] if " DECISION" in ln]

    def test_wait_sees_written_answer_as_closing_the_decision(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        self.assertEqual(len(self.open_decisions("--status")), 1)
        self.assertEqual(self.answer("legwork-151", "B"), 0)
        self.assertEqual(self.open_decisions("--status"), [])

    def test_wait_ignores_a_written_grant(self):
        self.write(BOARD["legwork-150"], BOARD["legwork-151"])
        before = self.open_decisions("--status")
        self.assertEqual(self.grant("answer the grill questions", "grill only"), 0)
        entries = self.wait.parse(str(self.board))[0]
        self.assertEqual([e[0] for e in entries], ["legwork-150", "legwork-151"])
        self.assertEqual(self.open_decisions("--status"), before)

    def test_wait_reports_next_decision_after_grant_and_answer(self):
        self.write(BOARD["legwork-151"])
        self.assertEqual(self.grant("go", "s"), 0)
        self.assertEqual(self.answer("legwork-151", "B"), 0)
        self.write(self.text(), "\n", BOARD["legwork-164"])
        self.assertEqual(self.open_decisions("--timeout", "1"),
                         ["legwork legwork-164 DECISION"])


if __name__ == "__main__":
    unittest.main()
