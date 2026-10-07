"""Read one legwork setting the way the orchestration skills do.

The skills read `<legwork>/config`, where `<legwork>` is $LEGWORK_DIR. The
scripts in bin/ may live in a different checkout from the queue, so they
look in the same places, in this order, and never write to the
environment:

  1. a real environment variable
  2. the file at $LEGWORK_CONFIG
  3. $LEGWORK_DIR/config
  4. `config` at the root of the checkout this file is in

Values get $VARS and ~ expanded, like the runner's load_config.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(1, str(ROOT / "core"))

from legwork_common import iter_config_pairs  # noqa: E402


def config_files(env=None):
    env = os.environ if env is None else env
    files = []
    if env.get("LEGWORK_CONFIG"):
        files.append(Path(os.path.expanduser(env["LEGWORK_CONFIG"])))
    if env.get("LEGWORK_DIR"):
        files.append(Path(os.path.expanduser(env["LEGWORK_DIR"])) / "config")
    files.append(ROOT / "config")
    return files


def setting(key, default=None, env=None):
    """The value of `key`, or `default` when no source sets it."""
    env = os.environ if env is None else env
    if env.get(key):
        return env[key]
    for path in config_files(env):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for k, v in iter_config_pairs(text):
            if k == key and v:
                return os.path.expanduser(os.path.expandvars(v))
    return default
