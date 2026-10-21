"""Tests for orchestration/bin/switchboard-context.

The script finds a Claude Code session's transcript under a Claude config
dir and prints the context in use from the last assistant message. Every
transcript here is a small fake one written by the test, under a temp
config dir, so no real session is read. The script runs as a subprocess
through sys.executable, so these run on Windows too.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SCRIPT = REPO / "orchestration" / "bin" / "switchboard-context"
SESSION = "11111111-2222-3333-4444-555555555555"


def load():
    loader = importlib.machinery.SourceFileLoader("switchboard_context", str(SCRIPT))
    spec = importlib.util.spec_from_loader("switchboard_context", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def assistant(inp=0, read=0, made=0, model="claude-test-1", side=False, **extra):
    entry = {"type": "assistant", "isSidechain": side, "sessionId": SESSION,
             "message": {"role": "assistant", "model": model,
                         "usage": {"input_tokens": inp, "cache_read_input_tokens": read,
                                   "cache_creation_input_tokens": made,
                                   "output_tokens": 50}}}
    entry.update(extra)
    return entry


def model_line(model_id):
    return {"type": "attachment", "isSidechain": False,
            "attachment": {"type": "model", "identity": {"modelId": model_id}}}


def checkout_copy(root):
    """The script, settings.py and the module it reads config with, copied
    into a bare checkout at `root`, so the script's last config source
    (<checkout>/config) is one the test owns, not the developer's."""
    for rel in ("orchestration/bin/switchboard-context", "orchestration/settings.py",
                "core/legwork_common.py"):
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(REPO / rel), str(dest))
    return root / "orchestration" / "bin" / "switchboard-context"


def user(text="hi"):
    return {"type": "user", "isSidechain": False,
            "message": {"role": "user", "content": text}}


class ContextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.config = self.tmp / "claude"
        self.work = self.tmp / "work dir"
        self.work.mkdir()
        self.mod = load()
        self.script = checkout_copy(self.tmp / "checkout")

    def write(self, entries, session=SESSION, folder=None, tail=""):
        folder = folder or self.mod.project_slug(str(self.work))
        path = self.config / "projects" / folder / f"{session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(e) for e in entries]
        path.write_text("\n".join(lines) + "\n" + tail, encoding="utf-8")
        return path

    def run_context(self, *args, session=SESSION, cwd=None, **env):
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("CLAUDE", "LEGWORK", "ANTHROPIC"))}
        base.update(CLAUDE_CONFIG_DIR=str(self.config),
                    LEGWORK_CONFIG=str(self.tmp / "no-config"))
        if session:
            base["CLAUDE_CODE_SESSION_ID"] = session
        base.update(env)
        done = subprocess.run([sys.executable, str(self.script), *args],
                              capture_output=True, text=True, env=base,
                              cwd=str(cwd or self.work))
        return done.returncode, done.stdout, done.stderr

    def assertOneLine(self, out, short=True):
        self.assertEqual(len(out.splitlines()), 1, out)
        if short:  # an error line names paths, which can be any length
            self.assertLessEqual(len(out.strip()), 160, out)

    # --- the figure -------------------------------------------------------

    def test_sums_the_three_input_fields_of_the_last_assistant_message(self):
        self.write([user(), assistant(5, 1000, 10), user(),
                    assistant(2, 120000, 300), user("tool result")])
        rc, out, err = self.run_context("--window", "1m")
        self.assertEqual(rc, 0, err)
        self.assertIn("120,302", out)
        self.assertIn("12.0%", out)
        self.assertOneLine(out)

    def test_skips_subagent_and_synthetic_messages_and_a_torn_last_line(self):
        self.write([assistant(1, 50000, 0),
                    assistant(1, 900000, 0, side=True),
                    assistant(0, 0, 0, model="<synthetic>")],
                   tail='{"type": "assistant", "message": {"usa')
        rc, out, err = self.run_context("--window", "200k")
        self.assertEqual(rc, 0, err)
        self.assertIn("50,001", out)

    def test_no_assistant_message_yet_reads_as_nothing_in_use(self):
        self.write([user("/clear")])
        rc, out, err = self.run_context()
        self.assertEqual(rc, 0, err)
        self.assertIn("no usage yet", out)
        self.assertOneLine(out)

    # --- the exit code ----------------------------------------------------

    def test_past_300k_cycles_on_a_1m_window(self):
        self.write([assistant(0, 310000, 0)])
        rc, out, _ = self.run_context("--window", "1000000")
        self.assertEqual(rc, 3, out)
        self.assertIn("cycle", out)
        self.write([assistant(0, 290000, 0)])
        rc, out, _ = self.run_context("--window", "1000000")
        self.assertEqual(rc, 0, out)
        self.assertNotRegex(out, r"\bcycle now\b")

    def test_past_60_percent_cycles_on_a_200k_window(self):
        self.write([assistant(0, 125000, 0)])
        rc, out, _ = self.run_context("--window", "200k")
        self.assertEqual(rc, 3, out)
        self.assertIn("62.5%", out)
        self.write([assistant(0, 115000, 0)])
        rc, out, _ = self.run_context("--window", "200k")
        self.assertEqual(rc, 0, out)

    def test_backstop_limits_replace_the_cycle_point(self):
        self.write([assistant(0, 350000, 0)])
        rc, out, _ = self.run_context("--window", "1m", "--limit", "400000",
                                      "--percent", "80")
        self.assertEqual(rc, 0, out)
        self.write([assistant(0, 170000, 0)])
        rc, out, _ = self.run_context("--window", "200k", "--limit", "400000",
                                      "--percent", "80")
        self.assertEqual(rc, 3, out)

    # --- the window -------------------------------------------------------

    def test_unknown_window_uses_the_300k_limit_alone_and_says_so(self):
        self.write([assistant(0, 150000, 0)])
        rc, out, _ = self.run_context()
        self.assertEqual(rc, 0, out)
        self.assertIn("window unknown", out)
        self.assertIn("300,000", out)
        self.assertNotIn("%", out)
        self.assertOneLine(out)
        self.write([assistant(0, 301000, 0)])
        rc, out, _ = self.run_context()
        self.assertEqual(rc, 3, out)
        self.assertIn("window unknown", out)

    def test_window_setting_is_read(self):
        self.write([assistant(0, 130000, 0)])
        rc, out, _ = self.run_context(LEGWORK_CONTEXT_WINDOW="200k")
        self.assertEqual(rc, 3, out)
        cfg = self.tmp / "config"
        cfg.write_text("LEGWORK_CONTEXT_WINDOW=1m\n", encoding="utf-8")
        rc, out, _ = self.run_context(LEGWORK_CONFIG=str(cfg))
        self.assertEqual(rc, 0, out)
        self.assertIn("13.0%", out)

    def test_the_checkout_config_is_read_last(self):
        self.write([assistant(0, 130000, 0)])
        (self.tmp / "checkout" / "config").write_text(
            "LEGWORK_CONTEXT_WINDOW=200k\n", encoding="utf-8")
        rc, out, _ = self.run_context()
        self.assertEqual(rc, 3, out)
        self.assertIn("65.0%", out)

    def test_a_1m_model_line_gives_the_window_however_far_back(self):
        # Claude Code names the model on an attachment line near the start
        # of a session; the assistant lines carry the plain model id.
        filler = [user(f"turn {i}") for i in range(1500)]
        self.write([model_line("claude-test-1[1m]"), *filler, assistant(0, 100000, 0)])
        rc, out, _ = self.run_context()
        self.assertEqual(rc, 0, out)
        self.assertIn("10.0%", out)

    def test_the_latest_model_line_decides(self):
        self.write([model_line("claude-test-1[1m]"), assistant(0, 100000, 0),
                    model_line("claude-test-1"), assistant(0, 100000, 0)])
        rc, out, _ = self.run_context()
        self.assertIn("window unknown", out)
        self.write([model_line("claude-test-1"), assistant(0, 100000, 0),
                    model_line("claude-test-1[1m]"), assistant(0, 100000, 0)])
        rc, out, _ = self.run_context()
        self.assertIn("10.0%", out)

    def test_a_1m_model_id_on_the_message_or_a_thinking_drop_gives_the_window(self):
        self.write([assistant(0, 100000, 0, model="claude-test-1[1m]")])
        rc, out, _ = self.run_context()
        self.assertIn("10.0%", out)
        drop = {"type": "attachment", "isSidechain": False,
                "attachment": {"type": "thinking_drop", "model": "claude-test-1[1m]"}}
        self.write([drop, assistant(0, 100000, 0)])
        rc, out, _ = self.run_context()
        self.assertIn("10.0%", out)

    def test_a_plain_model_id_is_not_taken_as_a_200k_window(self):
        # A plain model id can still run on a 1M window, so it tells nothing.
        self.write([model_line("claude-test-1"), assistant(0, 150000, 0)])
        rc, out, _ = self.run_context()
        self.assertEqual(rc, 0, out)
        self.assertIn("window unknown", out)

    def test_an_explicit_window_wins_over_a_marker(self):
        self.write([assistant(0, 100000, 0, model="claude-test-1[1m]")])
        rc, out, _ = self.run_context("--window", "200k")
        self.assertIn("50.0%", out)

    # --- finding the transcript -------------------------------------------

    def test_session_id_from_the_environment_is_found_under_any_folder(self):
        self.write([assistant(0, 42000, 0)], folder="some-other-folder")
        self.write([assistant(0, 99000, 0)], session="aaaaaaaa-0000-0000-0000-000000000000")
        rc, out, err = self.run_context()
        self.assertEqual(rc, 0, err)
        self.assertIn("42,000", out)

    def test_a_stale_session_id_gives_way_to_the_newest_transcript(self):
        # After a /clear the session id in the environment can name the old
        # transcript, last written well before the new one.
        old = self.write([assistant(0, 290000, 0)])
        past = time.time() - 600
        os.utime(old, (past, past))
        self.write([assistant(0, 12000, 0)], session="after-clear")
        rc, out, err = self.run_context()
        self.assertEqual(rc, 0, err)
        self.assertIn("12,000", out)

    def test_a_fresh_session_id_wins_over_a_slightly_newer_neighbour(self):
        # Another session in the same folder wrote a moment later: the own
        # transcript was written at this very tool call, so it is still read.
        own = self.write([assistant(0, 42000, 0)])
        other = self.write([assistant(0, 99000, 0)], session="neighbour")
        now = time.time()
        os.utime(own, (now - 5, now - 5))
        os.utime(other, (now, now))
        rc, out, err = self.run_context()
        self.assertEqual(rc, 0, err)
        self.assertIn("42,000", out)

    def test_a_stale_session_id_gives_way_soon_after_a_fast_cycle(self):
        # The health watcher cleared the session seconds after the old one
        # last wrote, so the new transcript leads the old by less than half
        # a minute. The old one was not written at this call, so it is stale.
        now = time.time()
        old = self.write([assistant(0, 310000, 0)])
        new = self.write([assistant(0, 12000, 0)], session="after-clear")
        os.utime(old, (now - 25, now - 25))
        os.utime(new, (now - 3, now - 3))
        rc, out, err = self.run_context()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("12,000", out)

    def test_session_flag_wins_over_the_environment(self):
        self.write([assistant(0, 42000, 0)])
        other = "bbbbbbbb-0000-0000-0000-000000000000"
        self.write([assistant(0, 77000, 0)], session=other)
        rc, out, _ = self.run_context("--session", other)
        self.assertIn("77,000", out)

    def test_without_a_session_id_the_newest_transcript_for_this_folder(self):
        old = self.write([assistant(0, 11000, 0)], session="old-session")
        past = time.time() - 600
        os.utime(old, (past, past))
        self.write([assistant(0, 22000, 0)], session="new-session")
        self.write([assistant(0, 33000, 0)], session="elsewhere", folder="x-other")
        rc, out, err = self.run_context(session=None)
        self.assertEqual(rc, 0, err)
        self.assertIn("22,000", out)

    def test_cwd_flag_reads_another_folder_and_ignores_the_own_session(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        self.write([assistant(0, 42000, 0)])
        self.write([assistant(0, 64000, 0)], session="orch-session",
                   folder=self.mod.project_slug(str(repo)))
        rc, out, err = self.run_context("--cwd", str(repo))
        self.assertEqual(rc, 0, err)
        self.assertIn("64,000", out)

    def test_no_transcript_exits_2_with_one_line(self):
        rc, out, err = self.run_context()
        self.assertEqual(rc, 2)
        self.assertIn("no transcript", out + err)
        self.assertOneLine(out + err, short=False)
        rc, out, err = self.run_context(session=None)
        self.assertEqual(rc, 2)

    def test_bad_window_exits_2(self):
        self.write([assistant(0, 1000, 0)])
        rc, out, err = self.run_context("--window", "lots")
        self.assertEqual(rc, 2, out)
        rc, out, err = self.run_context(LEGWORK_CONTEXT_WINDOW="lots")
        self.assertEqual(rc, 2, out)


class SlugTests(unittest.TestCase):
    def setUp(self):
        self.mod = load()

    def test_every_other_character_becomes_a_dash(self):
        slug = self.mod.project_slug
        self.assertEqual(slug("/src/my.repo"), "-src-my-repo")
        self.assertEqual(slug(r"D:\src\my_repo", windows=True), "D--src-my-repo")

    def test_git_bash_paths_on_windows_name_the_drive(self):
        self.assertEqual(self.mod.project_slug("/d/src/repo", windows=True),
                         "D--src-repo")
        self.assertEqual(self.mod.project_slug("D:/src/repo", windows=True),
                         "D--src-repo")

    def test_a_long_path_is_matched_by_its_first_200_characters(self):
        # Names only: a folder name this long is past Windows' path limit.
        long_path = "/src/" + "a" * 300
        stem = self.mod.project_slug(long_path)
        self.assertEqual(len(stem), 200)
        names = ["-src-other", stem + "-abc123"]
        self.assertEqual(self.mod.match_folder(stem, names), stem + "-abc123")
        self.assertIsNone(self.mod.match_folder("-src-short", ["-src-short-x"]))
        self.assertEqual(self.mod.match_folder("D--src", ["d--src"]), "d--src")

    def test_reads_backwards_across_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            path.write_bytes(b"one\ntwo two two\n\nthree\n")
            self.assertEqual(list(self.mod.lines_backwards(path, block=4)),
                             [b"three", b"", b"two two two", b"one"])


class DocTests(unittest.TestCase):
    def test_usage_names_the_exit_codes(self):
        doc = SCRIPT.read_text(encoding="utf-8").split('"""')[1]
        for needle in ("Usage:", "--session", "--cwd", "--window", "--limit",
                       "--percent", "LEGWORK_CONTEXT_WINDOW", "Exit 0", "3", "2"):
            self.assertIn(needle, doc)
        self.assertNotIn("\u2014", doc)
        self.assertIsNone(re.search(r"/Users/|C:\\Users", doc))


if __name__ == "__main__":
    unittest.main()
