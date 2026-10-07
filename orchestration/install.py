#!/usr/bin/env python3
"""Install the optional orchestration layer: the pieces past the manual loop.

Zero dependencies, like the rest of legwork. Run it from a checkout, usually
through the /onboard skill, which asks what you want first:

    python3 orchestration/install.py --with shipping,fanout,switchboard
    python3 orchestration/install.py --with all --hermes-profile work
    python3 orchestration/install.py --set LEGWORK_BUGBOT_OWNERS=my-org
    python3 orchestration/install.py --with fanout --dry-run

Pieces (--with, comma-separated, or `all`):

  shipping     /ship and /bugbot (land a branch as a PR, then the Bugbot loop)
  fanout       the fanout skill and /fanout (one orchestrator, many PRs)
  switchboard  the switchboard-protocol skill orchestrators follow
  herdr-skill  Herdr's own skill, from `herdr --skill`, if Herdr is installed
  usage-guard  a hook that tells long runs to pause before a usage limit
  hermes       the Hermes switchboard skill, and the house rules in SOUL.md

Claude Code pieces go under the Claude config dir: $CLAUDE_CONFIG_DIR if set,
else ~/.claude, or --claude-dir. Hermes pieces go into the Hermes profile
named by --hermes-profile (or LEGWORK_HERMES_PROFILE): `default` is
~/.hermes itself, any other name is ~/.hermes/profiles/<name>.

--set KEY=VALUE writes a setting into the legwork `config` file, replacing
the key if it is already there. Re-running is safe: files are overwritten
with the current copy, the hook is registered once, and the SOUL.md block is
replaced, not appended twice.

The functions that compute what to write (plan_copies, render,
set_config_keys, merge_usage_guard_hook, merge_soul) are pure, so the test
suite exercises them directly. Nothing runs until main() is called.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(1, str(REPO / "core"))

from legwork_common import iter_config_pairs, write_lf  # noqa: E402

PIECES = ("shipping", "fanout", "switchboard", "herdr-skill", "usage-guard",
          "hermes")

# piece -> files under orchestration/, mirrored under the Claude config dir.
CLAUDE_FILES = {
    "shipping": ["commands/ship.md", "commands/bugbot.md"],
    "fanout": ["commands/fanout.md", "skills/fanout/SKILL.md"],
    "switchboard": ["skills/switchboard-protocol/SKILL.md"],
}

HERMES_SKILL = "skills/orchestration/switchboard/SKILL.md"
CONFIG_SECTION = ("# --- Orchestration (OPTIONAL) ---------------------------"
                  "-----------------")
SOUL_START = "<!-- legwork house rules: start -->"
SOUL_END = "<!-- legwork house rules: end -->"
USAGE_EVENTS = ("UserPromptSubmit", "PostToolUse")


def parse_pieces(raw):
    """The pieces named in a --with value, in PIECES order. `all` is every
    piece. Raises ValueError on a name that is not a piece."""
    names = {p.strip() for p in (raw or "").split(",") if p.strip()}
    if "all" in names:
        return list(PIECES)
    unknown = sorted(names - set(PIECES))
    if unknown:
        raise ValueError(f"unknown piece(s): {', '.join(unknown)}")
    return [p for p in PIECES if p in names]


def claude_home(explicit=None, env=None):
    """The Claude config dir: an explicit path, else $CLAUDE_CONFIG_DIR,
    else ~/.claude."""
    env = os.environ if env is None else env
    raw = explicit or env.get("CLAUDE_CONFIG_DIR")
    return Path(os.path.expanduser(raw)) if raw else Path.home() / ".claude"


def hermes_home(profile, root=None):
    """A Hermes profile's folder: `default` is the Hermes root itself."""
    root = Path(root) if root else Path.home() / ".hermes"
    return root if profile in (None, "", "default") else root / "profiles" / profile


def plan_copies(pieces, claude_dir):
    """(source, destination) pairs for the Claude Code pieces. Read-only."""
    pairs = []
    for piece in pieces:
        for rel in CLAUDE_FILES.get(piece, []):
            pairs.append((HERE / rel, Path(claude_dir) / rel))
    return pairs


def render(text, legwork_dir, bin_dir=None, pane_config_dir=None):
    """Fill an installed file's placeholders. {{LEGWORK_DIR}} becomes the
    absolute legwork repo path; {{LEGWORK_BIN}} the absolute path of the
    scripts in this checkout (they need not live in the queue repo);
    {{PANE_ENV}} an --env flag that starts new orchestrator panes under a
    Claude config dir, or nothing for the default one. Fails if a
    placeholder is left over."""
    env = f" --env\n   CLAUDE_CONFIG_DIR={pane_config_dir}" if pane_config_dir else ""
    out = (text.replace("{{LEGWORK_DIR}}", str(legwork_dir))
           .replace("{{LEGWORK_BIN}}", str(bin_dir or HERE / "bin"))
           .replace("{{PANE_ENV}}", env))
    left = re.findall(r"\{\{[A-Z_]+\}\}", out)
    if left:
        raise ValueError(f"unfilled placeholder(s): {', '.join(sorted(set(left)))}")
    return out


def set_config_keys(text, pairs):
    """Return config text with each KEY=VALUE set. An active `KEY=` line is
    replaced in place; a key that is not active yet goes at the end of the
    orchestration section, which is added if missing. Comments and every
    other line are kept as they are."""
    lines = text.splitlines()
    for key, value in pairs:
        line = f"{key}={value}"
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        hits = [i for i, text in enumerate(lines) if pattern.match(text)]
        if hits:
            lines[hits[0]] = line
            for i in reversed(hits[1:]):
                del lines[i]
            continue
        if CONFIG_SECTION not in lines:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [CONFIG_SECTION,
                      "# Written by orchestration/install.py. See config.example."]
        at = lines.index(CONFIG_SECTION) + 1
        while at < len(lines) and lines[at].strip() and not lines[at].startswith("# ---"):
            at += 1
        lines.insert(at, line)
    return "\n".join(lines) + "\n"


def usage_guard_command(script):
    script = str(script)
    return f'"{script}"' if any(c.isspace() for c in script) else script


def merge_usage_guard_hook(settings, script):
    """A copy of a Claude settings dict with the usage guard registered on
    UserPromptSubmit and PostToolUse. Idempotent: an entry already running a
    usage-guard is re-pointed, never duplicated; other hooks are untouched."""
    out = json.loads(json.dumps(settings or {}))
    hooks = out.setdefault("hooks", {})
    command = usage_guard_command(script)
    for event in USAGE_EVENTS:
        entries = hooks.setdefault(event, [])
        found = False
        for entry in entries:
            for hook in entry.get("hooks", []):
                if "usage-guard" in str(hook.get("command", "")):
                    hook["command"] = command
                    found = True
        if not found:
            entry = {"hooks": [{"type": "command", "command": command,
                                "timeout": 5}]}
            if event == "PostToolUse":
                entry = {"matcher": "*", **entry}
            entries.append(entry)
    return out


def merge_soul(soul_text, house_rules):
    """SOUL.md text with the house rules in one marked block, replacing an
    earlier block rather than adding a second one."""
    rules = house_rules.strip()
    block = f"{SOUL_START}\nStanding preferences:\n{rules}\n{SOUL_END}"
    pattern = re.compile(re.escape(SOUL_START) + r".*?" + re.escape(SOUL_END), re.S)
    if pattern.search(soul_text):
        return pattern.sub(lambda _: block, soul_text, count=1)
    base = soul_text.rstrip("\n")
    return (base + "\n\n" if base else "") + block + "\n"


def house_rules_lines(text):
    """The rule lines of a house-rules.md: its list items, one line each. An
    indented line under an item continues it; headings, prose and blank
    lines are not rules."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ")):
            out.append("- " + s[2:].strip())
        elif s and out and line[:1].isspace():
            out[-1] += " " + s
        elif not s and out:
            out.append(None)  # a blank line ends the item
    return "\n".join(x for x in out if x is not None)


def unmarked_preferences(soul_text):
    """True when SOUL.md holds a "Standing preferences:" block outside the
    marked one, e.g. pasted in by hand before this installer existed."""
    pattern = re.compile(re.escape(SOUL_START) + r".*?" + re.escape(SOUL_END), re.S)
    return "Standing preferences:" in pattern.sub("", soul_text)


def parse_set(values):
    pairs = []
    for raw in values or []:
        key, sep, value = raw.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
            raise ValueError(f"--set wants KEY=VALUE, got {raw!r}")
        pairs.append((key.strip(), value.strip()))
    return pairs


def config_path(legwork_dir):
    return Path(os.environ.get("LEGWORK_CONFIG") or Path(legwork_dir) / "config")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--with", dest="pieces", default="",
                    help="comma-separated pieces, or all: " + ", ".join(PIECES))
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="write a setting into the legwork config (repeatable)")
    ap.add_argument("--claude-dir", help="Claude config dir (default "
                    "$CLAUDE_CONFIG_DIR, else ~/.claude)")
    ap.add_argument("--legwork-dir", help="the legwork repo (default "
                    "$LEGWORK_DIR, else this checkout)")
    ap.add_argument("--hermes-profile", help="Hermes profile for the hermes piece")
    ap.add_argument("--pane-config-dir", help="Claude config dir that new "
                    "orchestrator panes start under (default: --claude-dir "
                    "when it is not ~/.claude)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan only")
    args = ap.parse_args(argv)

    try:
        pieces = parse_pieces(args.pieces)
        pairs = parse_set(args.set)
    except ValueError as e:
        ap.error(str(e))

    legwork_dir = Path(os.path.expanduser(
        args.legwork_dir or os.environ.get("LEGWORK_DIR") or REPO)).resolve()
    claude_dir = claude_home(args.claude_dir)
    cfg_path = config_path(legwork_dir)
    existing = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    settings = dict(iter_config_pairs(existing))
    if "hermes" in pieces:
        profile = args.hermes_profile or settings.get("LEGWORK_HERMES_PROFILE") or "default"
        if not any(k == "LEGWORK_HERMES_PROFILE" for k, _ in pairs):
            pairs.append(("LEGWORK_HERMES_PROFILE", profile))
    if any(p in pieces for p in ("fanout", "switchboard")):
        if not any(k == "LEGWORK_ORCHESTRATION" for k, _ in pairs):
            pairs.append(("LEGWORK_ORCHESTRATION", "1"))

    done = []

    def say(line):
        print(("would " if args.dry_run else "") + line)
        done.append(line)

    if not os.environ.get("LEGWORK_DIR") and legwork_dir != (Path.home() / "legwork").resolve():
        print(f"note: the skills look for the legwork repo at $LEGWORK_DIR, else ~/legwork; "
              f"add 'export LEGWORK_DIR={legwork_dir}' to your shell profile")
    if any(p in pieces for p in ("shipping", "fanout", "hermes")) \
            and not (legwork_dir / "house-rules.md").exists():
        print(f"note: no {legwork_dir / 'house-rules.md'}; workers, /ship and /bugbot "
              "will have no house rules (see house-rules.example.md)")

    for src, dest in plan_copies(pieces, claude_dir):
        say(f"install {src.relative_to(REPO)} -> {dest}")
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_lf(dest, render(src.read_text(encoding="utf-8"), legwork_dir))

    if "herdr-skill" in pieces:
        dest = claude_dir / "skills" / "herdr" / "SKILL.md"
        herdr = shutil.which("herdr")
        if not herdr:
            print("skip herdr-skill: herdr is not on PATH (install it from herdr.dev)")
        else:
            say(f"write {dest} from `herdr --skill`")
            if not args.dry_run:
                text = subprocess.run([herdr, "--skill"], capture_output=True,
                                      text=True, timeout=30, check=True).stdout
                dest.parent.mkdir(parents=True, exist_ok=True)
                write_lf(dest, text)

    if "usage-guard" in pieces:
        script = HERE / "bin" / "usage-guard"
        settings_path = claude_dir / "settings.json"
        say(f"register {script} on {', '.join(USAGE_EVENTS)} in {settings_path}")
        if not args.dry_run:
            current = (json.loads(settings_path.read_text(encoding="utf-8"))
                       if settings_path.exists() else {})
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            write_lf(settings_path, json.dumps(
                merge_usage_guard_hook(current, script), indent=2) + "\n")

    if "hermes" in pieces:
        home = hermes_home(profile)
        pane_dir = args.pane_config_dir
        if pane_dir is None and claude_dir != Path.home() / ".claude":
            pane_dir = str(claude_dir)
        skill = render(
            (HERE / "hermes" / "switchboard" / "SKILL.md").read_text(encoding="utf-8"),
            legwork_dir, pane_config_dir=pane_dir)
        dest = home / HERMES_SKILL
        say(f"write {dest}")
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_lf(dest, skill)
        rules_file = legwork_dir / "house-rules.md"
        if rules_file.exists():
            rules = house_rules_lines(rules_file.read_text(encoding="utf-8"))
            soul = home / "SOUL.md"
            say(f"put the house rules in {soul}")
            before = soul.read_text(encoding="utf-8") if soul.exists() else ""
            if unmarked_preferences(before):
                print(f"note: {soul} already has a 'Standing preferences:' block outside "
                      "the legwork markers; remove it, or the rules appear twice")
            if not args.dry_run:
                write_lf(soul, merge_soul(before, rules))
        else:
            print(f"skip SOUL.md: no {rules_file}")

    if pairs:
        say(f"set {', '.join(k for k, _ in pairs)} in {cfg_path}")
        if not args.dry_run:
            write_lf(cfg_path, set_config_keys(existing, pairs))

    if not done:
        print("nothing to do: pass --with and/or --set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
