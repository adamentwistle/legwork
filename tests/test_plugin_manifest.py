"""The Claude Code plugin + marketplace manifests.

legwork ships the level-1 manual loop as a one-line-install plugin. The plugin
is not a copy of core/ -- it *is* core/: the plugin manifest lives at
``core/.claude-plugin/plugin.json`` and the marketplace entry sources the
plugin from ``./core``. So the plugin's command and skill surface is core's own
``commands/`` and ``skills/`` directories, auto-discovered, with nothing copied
that could drift.

These tests pin that invariant and, critically, the Phase 2 rule the whole
split exists to guarantee: the published lite/plugin artifact contains ZERO
suite/ code. Because the artifact is exactly core/, that reduces to "core/ has
no suite/ inside it and no manifest string reaches for one". The CI lite gate
runs this module against the full checkout before it strips to core/ alone.

Stdlib only, no fixtures: these are file reads.
"""

import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
PLUGIN = REPO / "core" / ".claude-plugin" / "plugin.json"

# The complete level-1 command surface. Adding a command to core/ is a
# deliberate act; this list makes it show up here too, so the plugin surface
# can never silently drift from what the repo and installer expose.
EXPECTED_COMMANDS = {"add", "wrap", "pickup", "log", "shelve", "vision"}
EXPECTED_SKILLS = {"legwork-tracker"}

# core/ is the whole plugin, so nothing from these worlds may live inside it,
# and no manifest string may point at one.
FORBIDDEN_TOKENS = ("suite/", "suite\\", "scripts/", "scripts\\", "..")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _strings(node):
    """Every string value anywhere in a JSON tree."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


class ManifestsAreWellFormed(unittest.TestCase):
    def test_both_manifests_exist_and_parse(self):
        self.assertTrue(MARKETPLACE.is_file(), f"missing {MARKETPLACE}")
        self.assertTrue(PLUGIN.is_file(), f"missing {PLUGIN}")
        _load(MARKETPLACE)
        _load(PLUGIN)


class PluginManifest(unittest.TestCase):
    def setUp(self):
        self.m = _load(PLUGIN)

    def test_required_and_identity_fields(self):
        # name is the one hard-required field; the rest we require by choice so
        # the marketplace listing is complete.
        self.assertEqual(self.m.get("name"), "legwork")
        for field in ("description", "version", "license"):
            self.assertTrue(self.m.get(field), f"plugin.json needs {field}")
        # A pinned semver, so users only see updates when it is bumped.
        parts = self.m["version"].split(".")
        self.assertEqual(len(parts), 3, "version should be semver x.y.z")
        self.assertTrue(all(p.isdigit() for p in parts), "semver parts numeric")

    def test_declares_no_component_paths(self):
        # The plugin exposes its surface by convention (core/commands,
        # core/skills), not by custom path fields. Custom paths would be a
        # second source of truth that could point somewhere core/ does not.
        for field in ("commands", "skills", "agents", "hooks", "mcpServers"):
            self.assertNotIn(
                field, self.m,
                f"plugin.json should not set '{field}'; discovery is by convention",
            )

    def test_no_forbidden_tokens(self):
        for s in _strings(self.m):
            for tok in FORBIDDEN_TOKENS:
                self.assertNotIn(tok, s, f"plugin.json string reaches out: {s!r}")


class MarketplaceManifest(unittest.TestCase):
    def setUp(self):
        self.m = _load(MARKETPLACE)

    def test_required_fields(self):
        self.assertTrue(self.m.get("name"), "marketplace needs a name")
        self.assertTrue(
            isinstance(self.m.get("owner"), dict) and self.m["owner"].get("name"),
            "marketplace needs owner.name",
        )
        self.assertIsInstance(self.m.get("plugins"), list)

    def test_single_plugin_sourced_from_core(self):
        plugins = self.m["plugins"]
        self.assertEqual(len(plugins), 1, "expose exactly the one legwork plugin")
        entry = plugins[0]
        self.assertEqual(entry.get("name"), "legwork")
        # ./core is the single source: it must resolve to the plugin manifest.
        self.assertEqual(entry.get("source"), "./core")
        resolved = (REPO / entry["source"] / ".claude-plugin" / "plugin.json").resolve()
        self.assertEqual(resolved, PLUGIN.resolve())

    def test_no_forbidden_tokens(self):
        # ./core is allowed; anything reaching into suite/ or scripts/ is not.
        for s in _strings(self.m):
            for tok in ("suite/", "scripts/", ".."):
                self.assertNotIn(tok, s, f"marketplace string reaches out: {s!r}")


class ArtifactIsCoreOnly(unittest.TestCase):
    """The plugin root is core/. Prove its exposed surface is exactly the
    level-1 loop and that no suite/autonomy code lives inside it."""

    def test_command_surface_is_exactly_the_loop(self):
        cmd_dir = REPO / "core" / "commands"
        found = {p.stem for p in cmd_dir.glob("*.md")}
        self.assertEqual(found, EXPECTED_COMMANDS)

    def test_skill_surface_is_the_tracker(self):
        skills_dir = REPO / "core" / "skills"
        found = {p.name for p in skills_dir.iterdir() if p.is_dir()}
        self.assertEqual(found, EXPECTED_SKILLS)
        self.assertTrue((skills_dir / "legwork-tracker" / "SKILL.md").is_file())

    def test_core_contains_no_suite_or_autonomy(self):
        # Directly: the plugin artifact (all of core/) has zero suite/ footprint.
        for name in ("suite", "scripts", "tests", "legwork_runner.py",
                     "legwork_review.py"):
            self.assertFalse(
                (REPO / "core" / name).exists(),
                f"core/{name} must not exist -- it would ship in the plugin",
            )


if __name__ == "__main__":
    unittest.main()
