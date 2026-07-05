"""The wedge build (scripts/build_wedge.py).

The wedge is the level-1 loop published as its own repo: a generated build
artifact of core/, one editable source, zero drift. These tests pin the two
properties that matter -- the built wedge/core/ is byte-identical to core/, and
nothing from suite/ ever leaks in -- plus the wrapper files (marketplace
sourced from ./core, a README that names the canonical source, LICENSE).

The pure builders are covered directly; build/verify run against a real
temp-dir build. Stdlib only, no third-party deps.
"""

import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent


def _load_build_wedge():
    spec = importlib.util.spec_from_file_location(
        "build_wedge", REPO / "scripts" / "build_wedge.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bw = _load_build_wedge()


class PureBuilders(unittest.TestCase):
    def test_readme_names_canonical_and_wedge_slugs(self):
        readme = bw.render_wedge_readme("someone/legwork-loop")
        # The wedge slug drives the install lines.
        self.assertIn("/plugin marketplace add someone/legwork-loop", readme)
        # The canonical source is always cross-linked (clone-tracing).
        self.assertIn(bw.CANONICAL_SLUG, readme)
        self.assertIn("generated build artifact", readme)
        self.assertIn("## Canonical source", readme)

    def test_gitignore_covers_artifacts_not_core(self):
        gi = bw.render_wedge_gitignore()
        self.assertIn("__pycache__/", gi)
        self.assertIn("dashboard/index.html", gi)
        # core/ is the payload; no ignore *rule* (non-comment line) may drop it.
        rules = [ln.strip() for ln in gi.splitlines()
                 if ln.strip() and not ln.startswith("#")]
        self.assertFalse(
            any("core" in r for r in rules),
            f"a gitignore rule would drop core/: {rules}",
        )


class BuildProducesAVerifiedWedge(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.out = Path(self._tmp.name) / "wedge"

    def tearDown(self):
        self._tmp.cleanup()

    def test_build_then_verify_is_clean(self):
        entries = bw.build_wedge(REPO, self.out, "someone/legwork-loop")
        self.assertEqual(
            set(entries),
            {"core", ".claude-plugin", "README.md", "LICENSE", ".gitignore"},
        )
        self.assertEqual(bw.verify_wedge(REPO, self.out), [])

    def test_wedge_core_is_byte_identical_to_core(self):
        bw.build_wedge(REPO, self.out, "someone/legwork-loop")
        src = set(bw._iter_core_files(REPO / "core"))
        dst = set(bw._iter_core_files(self.out / "core"))
        self.assertEqual(src, dst, "wedge/core/ file set differs from core/")
        self.assertTrue(src, "sanity: core/ is not empty")
        # verify_wedge does the byte comparison; a clean result proves identity.
        self.assertEqual(bw.verify_wedge(REPO, self.out), [])

    def test_marketplace_sources_core_and_names_the_plugin(self):
        bw.build_wedge(REPO, self.out, "someone/legwork-loop")
        m = json.loads(
            (self.out / ".claude-plugin" / "marketplace.json").read_text()
        )
        self.assertEqual(m["name"], "legwork")
        self.assertEqual(len(m["plugins"]), 1)
        entry = m["plugins"][0]
        self.assertEqual(entry["source"], "./core")
        # ./core must resolve to a real plugin manifest inside the wedge.
        resolved = (self.out / entry["source"] / ".claude-plugin"
                    / "plugin.json")
        self.assertTrue(resolved.is_file())
        self.assertEqual(json.loads(resolved.read_text())["name"], "legwork")

    def test_no_suite_or_scripts_leaks_in(self):
        bw.build_wedge(REPO, self.out, "someone/legwork-loop")
        for forbidden in ("suite", "scripts", "tests"):
            self.assertFalse(
                (self.out / forbidden).exists(),
                f"{forbidden}/ must not appear in the wedge",
            )
        # And no string anywhere in the manifest reaches for them.
        m = (self.out / ".claude-plugin" / "marketplace.json").read_text()
        for tok in ("suite/", "scripts/", ".."):
            self.assertNotIn(tok, m)


class BuildIsSafe(unittest.TestCase):
    def test_refuses_nonempty_output_without_force(self):
        with TemporaryDirectory() as d:
            out = Path(d) / "wedge"
            bw.build_wedge(REPO, out, "someone/legwork-loop")
            with self.assertRaises(FileExistsError):
                bw.build_wedge(REPO, out, "someone/legwork-loop")
            # force overwrites cleanly and still verifies.
            bw.build_wedge(REPO, out, "someone/legwork-loop", force=True)
            self.assertEqual(bw.verify_wedge(REPO, out), [])

    def test_verify_flags_drift(self):
        with TemporaryDirectory() as d:
            out = Path(d) / "wedge"
            bw.build_wedge(REPO, out, "someone/legwork-loop")
            # Tamper with a copied core file: verify must catch it.
            victim = out / "core" / "legwork_common.py"
            victim.write_text(victim.read_text() + "\n# drift\n")
            problems = bw.verify_wedge(REPO, out)
            self.assertTrue(
                any("legwork_common.py" in p for p in problems), problems
            )

    def test_verify_flags_missing_build(self):
        with TemporaryDirectory() as d:
            problems = bw.verify_wedge(REPO, Path(d) / "never-built")
            self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
