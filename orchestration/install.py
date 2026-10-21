#!/usr/bin/env python3
"""Install the optional orchestration layer: the pieces past the manual loop.

Zero dependencies, like the rest of legwork. Run it from a checkout, usually
through the /onboard skill, which asks what you want first:

    python3 orchestration/install.py --with shipping,fanout,switchboard
    python3 orchestration/install.py --with all
    python3 orchestration/install.py --set LEGWORK_BUGBOT_OWNERS=my-org
    python3 orchestration/install.py --with fanout --dry-run

On Windows, run it as `python orchestration/install.py ...`.

Pieces (--with, comma-separated, or `all`):

  shipping     /ship and /bugbot (land a branch as a PR, then the Bugbot loop)
  fanout       the fanout skill and /fanout (one orchestrator, many PRs)
  switchboard  the switchboard-protocol skill orchestrators follow, and, unless
               LEGWORK_SWITCHBOARD is manual, the switchboard skill (/switchboard,
               a Claude Code session in a Herdr pane) and roadmap-planning
  herdr-skill  Herdr's own skill, from `herdr --skill`, if Herdr is installed
  usage-guard  a hook that tells long runs to pause before a usage limit
  codex-agent  the codex subagent: OpenAI's Codex CLI as a cross-model second
               opinion, if the codex CLI is installed and signed in
  switchboard-health  lets the switchboard switch its health watcher on and
               off (macOS and Windows): writes LEGWORK_SWITCHBOARD_HEALTH=1
               after checking herdr and the system scheduler. It registers no
               job; the switchboard runs `switchboard-health-ctl on` itself
  pane-notify  Stop and Notification hooks that show a banner and play a sound
               from the panes worth hearing (macOS and Windows)

Each piece picks its own platform form: on Windows every script line and
hook names the interpreter, on macOS the scripts run by their first line. On
Linux, switchboard-health and pane-notify are skipped with a message.

Claude Code pieces go under the Claude config dir: $CLAUDE_CONFIG_DIR if set,
else ~/.claude, or --claude-dir. When that is not ~/.claude, the switchboard
skill opens new orchestrator panes under it too (--pane-config-dir changes
that).

LEGWORK_SWITCHBOARD is `claude` (the default) or `manual`. It is read from
--set, then the environment, then the legwork config. A config written for
an older switchboard is upgraded: its LEGWORK_SWITCHBOARD becomes `claude`
and the key in RETIRED_KEYS is dropped, with one note. That switchboard's
piece and its profile flag, from an older install command, are skipped with
one line, so a re-run of that command still refreshes everything else.

--set KEY=VALUE writes a setting into the legwork `config` file, replacing
the key if it is already there. Re-running is safe: files are overwritten
with the current copy and each hook is registered once.

The functions that compute what to write (plan_copies, render,
set_config_keys, upgrade_config, merge_usage_guard_hook,
merge_pane_notify_hook, health_check, pane_notify_check) are pure, so the
test suite exercises them directly. Nothing runs until main() is called.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(1, str(REPO / "core"))

from legwork_common import iter_config_pairs, switchboard_mode, write_lf  # noqa: E402

PIECES = ("shipping", "fanout", "switchboard", "herdr-skill", "usage-guard",
          "codex-agent", "switchboard-health", "pane-notify")

# piece -> files under orchestration/, mirrored under the Claude config dir.
CLAUDE_FILES = {
    "shipping": ["commands/ship.md", "commands/bugbot.md"],
    "fanout": ["commands/fanout.md", "skills/fanout/SKILL.md"],
    "switchboard": ["skills/switchboard-protocol/SKILL.md",
                    "skills/switchboard/SKILL.md",
                    "skills/roadmap-planning/SKILL.md"],
    "codex-agent": ["agents/codex.md"],
}

# The switchboard session's own files: left out when LEGWORK_SWITCHBOARD is
# manual, since then the user relays and no switchboard session runs.
SESSION_FILES = ("skills/switchboard/SKILL.md", "skills/roadmap-planning/SKILL.md")

# Keys an older install wrote for a switchboard legwork no longer ships.
RETIRED_KEYS = ("LEGWORK_HERMES_PROFILE",)
# Its piece name, read from the key so the name is spelt once.
RETIRED_PIECE = RETIRED_KEYS[0].split("_")[1].lower()
RETIRED_FLAG = f"--{RETIRED_PIECE}-profile"

CONFIG_SECTION = ("# --- Orchestration (OPTIONAL) ---------------------------"
                  "-----------------")
USAGE_EVENTS = ("UserPromptSubmit", "PostToolUse")
NOTIFY_EVENTS = ("Stop", "Notification")
NOTIFY_TIMEOUT = 10
HOMEBREW_NOTIFIER = "/opt/homebrew/bin/terminal-notifier"
SCHEDULERS = {"mac": "launchctl", "windows": "schtasks"}


def parse_pieces(raw):
    """The pieces named in a --with value, in PIECES order. `all` is every
    piece. RETIRED_PIECE is dropped (main() says so). Raises ValueError on
    any other name that is not a piece."""
    names = {p.strip() for p in (raw or "").split(",") if p.strip()} - {RETIRED_PIECE}
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


def pane_config_dir(explicit, claude_dir, home=None):
    """The Claude config dir new orchestrator panes start under, or None for
    the default one: --pane-config-dir when given (empty means none), else
    the install's Claude config dir when it is not ~/.claude."""
    if explicit is not None:
        return explicit or None
    default = (Path(home) if home else Path.home()) / ".claude"
    return None if Path(claude_dir) == default else str(claude_dir)


def platform_kind(platform=None):
    """`mac`, `windows` or `other`, from sys.platform."""
    platform = platform or sys.platform
    if platform == "darwin":
        return "mac"
    if platform.startswith("win"):
        return "windows"
    return "other"


def plan_copies(pieces, claude_dir, switchboard="claude"):
    """(source, destination) pairs for the Claude Code pieces. Read-only.
    A manual switchboard gets the protocol without the session's files."""
    manual = switchboard_mode(switchboard) == "manual"
    pairs = []
    for piece in pieces:
        for rel in CLAUDE_FILES.get(piece, []):
            if manual and rel in SESSION_FILES:
                continue
            pairs.append((HERE / rel, Path(claude_dir) / rel))
    return pairs


def script_python():
    """The interpreter installed lines name before a bin/ script, or None to
    let the script's shebang choose. Windows only: the shebangs say
    `python3`, which there is usually the Microsoft Store stub."""
    return hook_python() if os.name == "nt" else None


def render(text, legwork_dir, bin_dir=None, pane_config_dir=None, python=None):
    """Fill an installed file's placeholders. {{LEGWORK_DIR}} becomes the
    absolute legwork repo path; {{LEGWORK_BIN}} the absolute path of the
    scripts in this checkout (they need not live in the queue repo);
    {{PANE_ENV}} an --env flag that starts new orchestrator panes under a
    Claude config dir, or nothing for the default one. Fails if a
    placeholder is left over.

    Paths go in with forward slashes: Claude runs these lines through bash,
    which is Git Bash on Windows, and an unquoted E:\\dev\\... reaches it with
    every backslash eaten. Forward slashes work in Git Bash and in Windows
    file APIs alike, and change nothing on POSIX.

    A {{LEGWORK_BIN}}/<script> call also gets the interpreter in front when
    there is one (`python`, else script_python()), POSIX-quoted, so it runs
    without the shebang. On POSIX there is none and the line is unchanged."""
    env = (f" --env\n   CLAUDE_CONFIG_DIR={Path(pane_config_dir).as_posix()}"
           if pane_config_dir else "")
    bin_path = Path(bin_dir or HERE / "bin").as_posix()
    python = python or script_python()

    def script(match):
        path = f"{bin_path}/{match.group(1)}"
        return shlex.join([Path(python).as_posix(), path]) if python else path

    out = re.sub(r"\{\{LEGWORK_BIN\}\}/([\w.-]+)", script, text)
    out = (out.replace("{{LEGWORK_DIR}}", Path(legwork_dir).as_posix())
           .replace("{{LEGWORK_BIN}}", bin_path)
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


def upgrade_config(text):
    """(text, changed): config text with an older switchboard's settings
    brought up to date. An active LEGWORK_SWITCHBOARD that is neither
    `claude` nor `manual` becomes `claude`, and active RETIRED_KEYS lines go.
    `changed` names each key that changed, in that order, or is empty.
    Comments and every other line are kept as they are."""
    out, changed = [], set()
    for line in text.splitlines(keepends=True):
        key, sep, value = line.strip().partition("=")
        key = key.strip()
        if sep and not key.startswith("#"):
            if key in RETIRED_KEYS:
                changed.add(key)
                continue
            value = value.strip().strip('"').strip("'").strip().lower()
            if key == "LEGWORK_SWITCHBOARD" and value not in ("", "claude", "manual"):
                ending = line[len(line.rstrip("\r\n")):] or "\n"
                out.append("LEGWORK_SWITCHBOARD=claude" + ending)
                changed.add(key)
                continue
        out.append(line)
    order = ("LEGWORK_SWITCHBOARD",) + RETIRED_KEYS
    return "".join(out), tuple(k for k in order if k in changed)


def upgrade_note(cfg_path, changed):
    """The one note line for what upgrade_config changed, or None."""
    parts = []
    if "LEGWORK_SWITCHBOARD" in changed:
        parts.append("LEGWORK_SWITCHBOARD now reads claude")
    if any(k in changed for k in RETIRED_KEYS):
        parts.append("the old profile key goes")
    if not parts:
        return None
    return (f"note: {cfg_path} had settings for a switchboard legwork no longer "
            f"ships; {' and '.join(parts)}")


def given_switchboard(value):
    """(value to write, note or None) for a --set LEGWORK_SWITCHBOARD value.
    `claude` and `manual` go in as lowercase; RETIRED_PIECE goes in as
    `claude` with a note. Raises ValueError on anything else."""
    word = value.strip().strip('"').strip("'").strip().lower()
    if word in ("claude", "manual"):
        return word, None
    if word == RETIRED_PIECE:
        return "claude", (f"note: legwork no longer ships the {word} switchboard; "
                          "writing LEGWORK_SWITCHBOARD=claude")
    raise ValueError(f"LEGWORK_SWITCHBOARD takes claude or manual, got {value!r}")


def hook_python():
    """The interpreter the usage-guard hook runs under, chosen like the
    wizard's detect_python: on Windows the interpreter running this
    installer, since `python3` there is usually the Microsoft Store stub."""
    if os.name != "nt":
        if Path("/usr/bin/python3").exists():
            return "/usr/bin/python3"
        found = shutil.which("python3")
        if found:
            return found
    return sys.executable or "python"


def usage_guard_command(script, python=None):
    """The hook's shell-form command: interpreter plus script, POSIX-quoted
    on every platform because Claude Code runs hooks through bash (Git Bash
    on Windows). Same rule as hook_command in scripts/legwork_install.py."""
    return shlex.join([python or hook_python(), str(script)])


def pane_notify_command(bin_dir=None, python=None):
    """The pane-notify hook's command, the {{LEGWORK_BIN}}/pane-notify line
    render() would write: slashed for Git Bash, and on Windows with the
    interpreter in front (`python`, else script_python()), as the
    usage-guard hook has it. On POSIX the script runs by its first line."""
    script = f"{Path(bin_dir or HERE / 'bin').as_posix()}/pane-notify"
    python = python or script_python()
    return shlex.join(([Path(python).as_posix()] if python else []) + [script])


def _merge_hook(settings, events, marker, command, timeout, matchers=()):
    """A copy of a Claude settings dict with `command` registered on each
    event. Idempotent: an entry whose command names `marker` is re-pointed,
    never duplicated; other hooks are untouched. Events in `matchers` get a
    `*` matcher on a new entry."""
    out = json.loads(json.dumps(settings or {}))
    hooks = out.setdefault("hooks", {})
    for event in events:
        entries = hooks.setdefault(event, [])
        found = False
        for entry in entries:
            for hook in entry.get("hooks", []):
                if marker in str(hook.get("command", "")):
                    hook["command"] = command
                    found = True
        if not found:
            entry = {"hooks": [{"type": "command", "command": command,
                                "timeout": timeout}]}
            if event in matchers:
                entry = {"matcher": "*", **entry}
            entries.append(entry)
    return out


def merge_usage_guard_hook(settings, script):
    """The usage guard registered on UserPromptSubmit and PostToolUse."""
    return _merge_hook(settings, USAGE_EVENTS, "usage-guard",
                       usage_guard_command(script), 5, matchers=("PostToolUse",))


def merge_pane_notify_hook(settings, command):
    """pane-notify registered on Stop and Notification, timeout 10."""
    return _merge_hook(settings, NOTIFY_EVENTS, "pane-notify", command,
                       NOTIFY_TIMEOUT)


def health_check(kind, which, switchboard, legwork_dir="the legwork repo"):
    """(skip, notes) for the switchboard-health piece. `skip` is the reason
    not to install it, or None; `notes` are things the user should know.
    `kind` is platform_kind(); `which` looks a tool up on PATH."""
    if kind not in SCHEDULERS:
        return ("the health watcher runs on macOS (launchd) and Windows "
                "(Task Scheduler) only", [])
    if switchboard_mode(switchboard) == "manual":
        return ("LEGWORK_SWITCHBOARD is manual, so no switchboard session "
                "runs for it to watch", [])
    if not which("herdr"):
        return ("herdr is not on PATH; the watcher finds the switchboard "
                "through it (install it from herdr.dev)", [])
    scheduler = SCHEDULERS[kind]
    if not which(scheduler):
        return (f"{scheduler} is not on PATH; the watcher is scheduled "
                "with it", [])
    notes = []
    if not which("claude"):
        where = ("on PATH or under ~/.nvm/versions/node/*/bin" if kind == "mac"
                 else "on your user PATH")
        notes.append(f"claude is not on PATH: the watcher restarts the switchboard "
                     f"with it, so it must be {where} when the switchboard runs "
                     "switchboard-health-ctl on")
    notes.append(f"the watcher restarts the switchboard in {legwork_dir}; that folder "
                 "must already be trusted in Claude Code, or the restart stops "
                 "at the trust question")
    return None, notes


def pane_notify_check(kind, which, exists=os.path.exists):
    """(skip, notes) for the pane-notify piece, as health_check."""
    if kind not in SCHEDULERS:
        return "pane-notify shows banners on macOS and Windows only", []
    notes = []
    if not which("herdr"):
        notes.append("herdr is not on PATH: pane-notify stays silent outside "
                     "Herdr panes")
    if kind == "mac" and not (which("terminal-notifier") or exists(HOMEBREW_NOTIFIER)):
        notes.append("no terminal-notifier: install it with "
                     "`brew install terminal-notifier` for the banners")
    config = ("~/.config/herdr/config.toml" if kind == "mac"
              else r"%APPDATA%\herdr\config.toml")
    notes.append(f"switch Herdr's own toasts and Claude sounds off in {config} "
                 '([ui.toast] delivery = "off" and [ui.sound.agents] claude = "off"), '
                 "then run `herdr server reload-config`, or each one comes twice")
    return None, notes


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
    ap.add_argument("--pane-config-dir", help="Claude config dir that new "
                    "orchestrator panes start under (default: --claude-dir "
                    "when it is not ~/.claude)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan only")
    ap.add_argument(RETIRED_FLAG, dest="retired_profile", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    set_notes = []
    try:
        pieces = parse_pieces(args.pieces)
        pairs = parse_set(args.set)
        for i, (key, value) in enumerate(pairs):
            if key == "LEGWORK_SWITCHBOARD":
                value, note = given_switchboard(value)
                pairs[i] = (key, value)
                set_notes += [note] if note else []
    except ValueError as e:
        ap.error(str(e))
    named = {p.strip() for p in args.pieces.split(",")}
    retired = ([RETIRED_PIECE] if RETIRED_PIECE in named else []) + \
        ([RETIRED_FLAG] if args.retired_profile is not None else [])
    if retired:
        print(f"skip {' and '.join(retired)}: legwork no longer ships that "
              "switchboard; the switchboard piece installs the Claude Code one")

    legwork_dir = Path(os.path.expanduser(
        args.legwork_dir or os.environ.get("LEGWORK_DIR") or REPO)).resolve()
    claude_dir = claude_home(args.claude_dir)
    cfg_path = config_path(legwork_dir)
    existing = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    existing, upgraded = upgrade_config(existing)
    settings = dict(iter_config_pairs(existing))
    given = dict(pairs)
    switchboard = switchboard_mode(given.get("LEGWORK_SWITCHBOARD",
                                             os.environ.get("LEGWORK_SWITCHBOARD",
                                                            settings.get("LEGWORK_SWITCHBOARD"))))
    if any(p in pieces for p in ("fanout", "switchboard")):
        if "LEGWORK_ORCHESTRATION" not in given:
            pairs.append(("LEGWORK_ORCHESTRATION", "1"))
    kind = platform_kind()

    done = []

    def say(line):
        print(("would " if args.dry_run else "") + line)
        done.append(line)

    note = upgrade_note(cfg_path, upgraded)
    for line in ([note] if note else []) + set_notes:
        print(line)
    if not os.environ.get("LEGWORK_DIR") and legwork_dir != (Path.home() / "legwork").resolve():
        print(f"note: the skills look for the legwork repo at $LEGWORK_DIR, else ~/legwork; "
              f"add 'export LEGWORK_DIR={legwork_dir}' to your shell profile")
    planning = "switchboard" in pieces and switchboard != "manual"
    if (planning or any(p in pieces for p in ("shipping", "fanout"))) \
            and not (legwork_dir / "house-rules.md").exists():
        print(f"note: no {legwork_dir / 'house-rules.md'}; workers, /ship, /bugbot "
              "and planning sessions will have no house rules (see "
              "house-rules.example.md)")

    pane_dir = pane_config_dir(args.pane_config_dir, claude_dir)
    for src, dest in plan_copies(pieces, claude_dir, switchboard):
        say(f"install {src.relative_to(REPO)} -> {dest}")
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_lf(dest, render(src.read_text(encoding="utf-8"), legwork_dir,
                                  pane_config_dir=pane_dir))
    if "switchboard" in pieces and switchboard == "manual":
        print("skip the switchboard and roadmap-planning skills: "
              "LEGWORK_SWITCHBOARD is manual")
        for rel in SESSION_FILES:
            if (claude_dir / rel).exists():
                print(f"note: {claude_dir / rel} is left from a Claude Code "
                      "switchboard; delete it if you no longer want it")

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

    settings_path = claude_dir / "settings.json"

    def update_settings(merge):
        if args.dry_run:
            return
        current = (json.loads(settings_path.read_text(encoding="utf-8"))
                   if settings_path.exists() else {})
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        write_lf(settings_path, json.dumps(merge(current), indent=2) + "\n")

    if "usage-guard" in pieces:
        script = HERE / "bin" / "usage-guard"
        say(f"register {script} on {', '.join(USAGE_EVENTS)} in {settings_path}")
        update_settings(lambda s: merge_usage_guard_hook(s, script))

    if "switchboard-health" in pieces:
        skip, notes = health_check(kind, shutil.which, switchboard, legwork_dir)
        if skip:
            print(f"skip switchboard-health: {skip}")
        else:
            for note in notes:
                print(f"note: {note}")
            if "LEGWORK_SWITCHBOARD_HEALTH" not in given:
                pairs.append(("LEGWORK_SWITCHBOARD_HEALTH", "1"))
            say("let the switchboard switch its health watcher on and off "
                "(switchboard-health-ctl); no job is registered now")

    if "pane-notify" in pieces:
        skip, notes = pane_notify_check(kind, shutil.which)
        if skip:
            print(f"skip pane-notify: {skip}")
        else:
            for note in notes:
                print(f"note: {note}")
            command = pane_notify_command()
            say(f"register {command} on {', '.join(NOTIFY_EVENTS)} in {settings_path}")
            update_settings(lambda s: merge_pane_notify_hook(s, command))

    if pairs or upgraded:
        keys = [k for k, _ in pairs] or ["the switchboard settings"]
        say(f"set {', '.join(keys)} in {cfg_path}")
        if not args.dry_run:
            write_lf(cfg_path, set_config_keys(existing, pairs) if pairs else existing)

    if not done:
        print("nothing to do: pass --with and/or --set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
