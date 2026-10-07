---
description: Unattended orchestrator run for a legwork project. Load the queue, plan PR-sized tasks, dispatch to Herdr panes or subagents, land each PR through the gate and Bugbot, clean up, wrap.
---

Run the fanout skill for: $ARGUMENTS

Flags after the project name: `--merge` (merge clean PRs in order; default
leaves them open and rebased), `--low-priority` (set the mode in each pane
before its brief), `--max N`, `--only "item, item"`, `--resume` (continue
from .legwork/fanout-state.md).

Follow the skill, using your own judgement where it hands you a choice. Do not ask me anything; I am not here. Decide,
record the decision in the PR body or the state file, and carry on.
