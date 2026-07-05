#!/usr/bin/env python3
"""Generate the standalone "wedge" repo from core/.

The wedge is the level-1 manual loop published as its own repository, so it
can be shared on its own (internally at work today, as the public on-ramp
later) without a visitor ever seeing suite/, the n8n JSON or the launchd
plist -- the parts that read "enterprise-y" on a ten-second skim. It is a
**generated build artifact**, exactly like `dashboard/index.html`: one
editable source (core/), zero drift. Never hand-edit the output; change
core/ and rebuild.

    python3 scripts/build_wedge.py              build into dist/wedge/
    python3 scripts/build_wedge.py OUT          build into OUT
    python3 scripts/build_wedge.py --check      verify an existing build
                                                still matches core/ (no write)
    python3 scripts/build_wedge.py --repo-slug OWNER/NAME
                                                the wedge's own GitHub slug,
                                                woven into its README/install

The output tree is a self-contained Claude Code plugin repo:

    <out>/
      core/                        <- byte-identical copy of this repo's core/
      .claude-plugin/
        marketplace.json           <- sources ./core, one-line install
      README.md                    <- generated, with the canonical-source note
      LICENSE                      <- copied verbatim
      .gitignore

The `core/` subdirectory is preserved rather than flattened on purpose: every
`core/...` path reference inside the loop (the SessionEnd hook's builder path,
the commands' dashboard-rebuild line, the plugin manifest's "this plugin is
core/ itself") stays literally true, so the copy is verbatim and the zero-drift
check is a plain byte comparison with nothing to transform and get wrong. It is
also exactly how the monorepo's own marketplace sources the plugin (`./core`),
so the installed artifact is identical -- the wedge only changes what a browser
of the repo sees around it.

Stdlib only, like everything under core/, suite/ and scripts/. Importing this
module has no side effects: the builders are pure and main() runs only under
__main__.
"""

import argparse
import filecmp
import json
import shutil
import sys
from pathlib import Path

# This repo is the canonical source of truth. The wedge's README points back
# here so a reader (or a fork/clone that shows up in search) can find the
# origin, per the distribution doc's malware-clone mitigation.
CANONICAL_SLUG = "adamentwistle/legwork"

# The wedge's own repository slug. A placeholder default: the real name is a
# human call made when the repo is created, so it is overridable with
# --repo-slug and surfaced in the build summary as something to confirm.
DEFAULT_WEDGE_SLUG = "adamentwistle/legwork-loop"

# core/ is copied whole except for local bytecode. These match .gitignore.
_COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def _iter_core_files(core_dir):
    """Every real file under core/, excluding bytecode, as repo-relative
    paths. The unit of the zero-drift check."""
    for p in sorted(core_dir.rglob("*")):
        if p.is_dir():
            continue
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        yield p.relative_to(core_dir)


def render_wedge_gitignore():
    """The wedge is a plugin repo, not a tracker: it has no projects/ of its
    own, but running the dashboard builder or the Python leaves artifacts a
    clone should not commit."""
    return (
        "# macOS\n"
        ".DS_Store\n\n"
        "# Python bytecode from running core/'s stdlib scripts\n"
        "__pycache__/\n\n"
        "# The dashboard is a build artifact, rebuilt from your projects/\n"
        "dashboard/index.html\n\n"
        "# Your real project data lives in your own private tracker, not here\n"
        "/projects/\n"
    )


def render_wedge_readme(repo_slug, canonical_slug=CANONICAL_SLUG):
    """The wedge's front door. It leads with the loop and the one-line install,
    carries the GENERATED banner so no one edits the artifact, and states the
    canonical source so a clone can always be traced home."""
    return f"""# legwork

> **This repository is a generated build artifact.** Its source of truth is
> [`{canonical_slug}`](https://github.com/{canonical_slug}) — the `core/`
> directory there. Do not edit files here; changes belong in that repo and are
> republished by its `scripts/build_wedge.py`. See [Canonical source](#canonical-source).

legwork is a project queue for Claude Code that survives you walking away. Each
project is one markdown file with a status, an append-only log, and a
ready-to-run next prompt. You end a work session with `/wrap`, which records
what happened and writes the prompt your next session should start from, while
the context is still hot. Days later, `/pickup` briefs you back into the
project in thirty seconds instead of twenty minutes of re-reading. A static
dashboard shows the whole queue at a glance.

## Install

From inside Claude Code, add the marketplace and install the plugin:

```
/plugin marketplace add {repo_slug}
/plugin install legwork@legwork
```

That gives you the six slash commands (`/add`, `/wrap`, `/pickup`, `/log`,
`/shelve`, `/vision`) and the legwork-tracker skill in every repo on your
machine, backed by a queue in `~/legwork` (set `LEGWORK_DIR` to move it).

## The loop

The core of legwork is a habit, not a daemon: never end a session without
writing down what the next session should do.

```
/add      create projects/<name>.md: status, description, a real first prompt
 work     any Claude Code session, in any repo
/wrap     log what happened, write the next prompt while context is hot
 away     days pass; the dashboard shows every project and its next step
/pickup   a 30-second re-entry briefing; run the queued prompt or adjust it
```

The plugin's whole surface is the [`core/`](core/) directory: the commands,
the legwork-tracker skill, a standard-library dashboard builder
(`core/build_dashboard.py`), and the session hooks. No server, no
dependencies.

## Canonical source

This repo is the level-1 manual loop, published on its own. The full project —
including the optional level-2 runner and LLM reviewer that fire queued prompts
as unattended sessions — lives at
**[`{canonical_slug}`](https://github.com/{canonical_slug})**, which is also
where `core/` is authored. If you found a copy of this loop somewhere else,
that is the original. Install only from `{repo_slug}` or the canonical repo.

## License

MIT — see [LICENSE](LICENSE).
"""


def verify_wedge(repo_root, out_dir):
    """Return a list of drift problems: files that differ, are missing, or are
    extra between core/ and the built wedge/core/, plus any forbidden dir that
    leaked in. Empty list means the artifact still matches its source.

    Used by `--check` and by the test suite so a stale committed/pushed wedge
    can never silently diverge from core/.
    """
    core_dir = repo_root / "core"
    wedge_core = out_dir / "core"
    problems = []

    if not wedge_core.is_dir():
        return [f"missing {wedge_core}: nothing built here yet"]

    src = set(_iter_core_files(core_dir))
    dst = set(_iter_core_files(wedge_core))
    for rel in sorted(src - dst):
        problems.append(f"missing from wedge: core/{rel.as_posix()}")
    for rel in sorted(dst - src):
        problems.append(f"extra in wedge (not in core/): core/{rel.as_posix()}")
    for rel in sorted(src & dst):
        if not filecmp.cmp(core_dir / rel, wedge_core / rel, shallow=False):
            problems.append(f"drifted from core/: core/{rel.as_posix()}")

    # The wedge exists to hide these worlds; a leak would defeat its purpose.
    for forbidden in ("suite", "scripts", "tests"):
        if (out_dir / forbidden).exists():
            problems.append(f"forbidden dir leaked into wedge: {forbidden}/")

    return problems


def build_wedge(repo_root, out_dir, repo_slug=DEFAULT_WEDGE_SLUG, force=False):
    """Generate the wedge repo at out_dir from repo_root. Returns the list of
    top-level entries written. Refuses a non-empty out_dir unless force, so a
    stray path is never clobbered by accident."""
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    core_dir = repo_root / "core"
    marketplace = repo_root / ".claude-plugin" / "marketplace.json"
    license_file = repo_root / "LICENSE"

    for required in (core_dir, marketplace, license_file):
        if not required.exists():
            raise FileNotFoundError(f"cannot build wedge: missing {required}")

    if out_dir.exists() and any(out_dir.iterdir()):
        if not force:
            raise FileExistsError(
                f"{out_dir} is not empty; pass --force to overwrite it"
            )
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. core/ copied verbatim -- the single source, zero transform.
    shutil.copytree(core_dir, out_dir / "core", ignore=_COPY_IGNORE)

    # 2. The marketplace manifest, reused verbatim: it already sources ./core
    #    and names the legwork plugin, so it is correct for the wedge unchanged.
    #    Copying (not re-emitting) keeps it a single source too. Parse first so
    #    a corrupt source manifest fails the build loudly.
    json.loads(marketplace.read_text(encoding="utf-8"))
    (out_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(marketplace, out_dir / ".claude-plugin" / "marketplace.json")

    # 3. LICENSE verbatim.
    shutil.copy2(license_file, out_dir / "LICENSE")

    # 4. Generated wrappers.
    (out_dir / "README.md").write_text(
        render_wedge_readme(repo_slug), encoding="utf-8"
    )
    (out_dir / ".gitignore").write_text(
        render_wedge_gitignore(), encoding="utf-8"
    )

    return sorted(p.name for p in out_dir.iterdir())


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate the standalone wedge repo from core/.",
    )
    parser.add_argument(
        "out", nargs="?", default="dist/wedge",
        help="output directory (default: dist/wedge, a gitignored build artifact)",
    )
    parser.add_argument(
        "--repo-slug", default=DEFAULT_WEDGE_SLUG,
        help=f"the wedge repo's own OWNER/NAME slug for its README/install "
             f"(default: {DEFAULT_WEDGE_SLUG}, a placeholder to confirm)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="verify an existing build still matches core/; write nothing",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite a non-empty output directory",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = repo_root / out_dir

    if args.check:
        problems = verify_wedge(repo_root, out_dir)
        if problems:
            print(f"wedge at {out_dir} has drifted from core/:")
            for p in problems:
                print(f"  - {p}")
            return 1
        print(f"wedge at {out_dir} matches core/ (no drift)")
        return 0

    try:
        entries = build_wedge(repo_root, out_dir, args.repo_slug, args.force)
    except (FileExistsError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    problems = verify_wedge(repo_root, out_dir)
    if problems:  # a build that does not verify is a bug in this script
        print("built, but the result does not verify:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print(f"built wedge -> {out_dir}")
    print(f"  entries: {', '.join(entries)}")
    print(f"  repo slug in README/install: {args.repo_slug}")
    if args.repo_slug == DEFAULT_WEDGE_SLUG:
        print("  note: --repo-slug is the placeholder default; confirm the "
              "real name before the repo is created")
    print("  this is a build artifact; edit core/ and rebuild, never edit here")
    return 0


if __name__ == "__main__":
    sys.exit(main())
