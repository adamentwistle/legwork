---
name: roadmap-planning
description: Use when preparing a roadmap or running a planning session for a legwork project, such as "plan the next roadmap for <project>", "prepare a roadmap", or any session whose output is a design doc, a tracker Roadmap N and a queued /fanout prompt.
---

# Roadmap planning

Roadmaps are prepared in a planning session, separate from the switchboard.
The switchboard is execution only. A planning session works in the
project's repo and ends by queueing a `/fanout` prompt for the switchboard
to start. Load the skills `grilling`, `domain-modeling` and
`legwork-tracker` for it, when you have them; use those, not copies.

## Settings

The legwork repo is `{{LEGWORK_DIR}}`. Its settings are KEY=VALUE lines in
`{{LEGWORK_DIR}}/config`; a real environment variable wins over the file.
The ones this skill reads:

- `LEGWORK_OWN_OWNERS`: the GitHub users or orgs whose repos are the user's
  own. A shared repo is any owner not in `LEGWORK_OWN_OWNERS`.
- `LEGWORK_LINEAR_TEAM`, `LEGWORK_LINEAR_TEAM_KEY` and
  `LEGWORK_LINEAR_SWEEP_LABELS`: the Linear team, its issue key, and the
  comma-separated labels that mark the user's commitments filed from
  elsewhere (chat, notes). Unset team means no Linear sweep.
- `LEGWORK_SWITCHBOARD`: `manual` means the user relays by hand, so no
  switchboard session starts the run.

House rules: every list item of `{{LEGWORK_DIR}}/house-rules.md` applies to
each commit, pull request and comment this session writes (the design doc
and its pull request in step 4, the legwork commit in step 5), as it does
for `/ship`. If the file does not exist, say so once and carry on.

## Steps

1. Inputs: the tracker file `projects/<project>.md` in `{{LEGWORK_DIR}}`
   (Vision, last log entries), the repo's `CONTEXT.md` and `docs/adr/`, and
   whatever the user brings (feedback, a roadmap doc, known gaps, a chat
   thread). Look up facts yourself; put only decisions to the user.

   Then sweep Linear in full, every session, with the Linear MCP directly.
   Skip the sweep when `LEGWORK_LINEAR_TEAM` is unset. Sweep the
   `LEGWORK_LINEAR_TEAM` team: states Triage, Todo, Backlog and In Progress,
   issues in no project, and the `LEGWORK_LINEAR_SWEEP_LABELS` labels when
   set. The sweep is read-only. Show the user a cross-check table, one line
   per issue with the reason. A picked issue's id
   (`<LEGWORK_LINEAR_TEAM_KEY>-NN`) goes on the roadmap item. At the end of
   the session, propose one batched update for a single yes: picked issues
   to Todo in the right project, duplicates closed with a comment. Write to
   Linear only after the user says yes to that batch, or if their message
   asks for writes.

2. First time on a project with no `## Vision`: run the `legwork-tracker`
   Vision step before grilling. Never run a project on the switchboard
   before this check: the repo has a PR gate and allow rules for its test
   and build commands, and `.legwork/` is gitignored. If not, make the gate
   roadmap item 1.

   Also check that the repo's gitignored `.claude/settings.local.json`
   allows `Bash(gh pr create:*)`, `Bash(gh pr edit:*)` and
   `Bash(git push origin fanout/*)` pushes from worktrees, plus
   `Bash(gh pr merge:*)` only on repos whose owner is in
   `LEGWORK_OWN_OWNERS`, and only when the roadmap says merge; and that it
   denies `git push origin main`, `git push --force` and `git push -f`. Add
   missing rules now, and never commit them on a shared repo. With the
   merge rule in place the orchestrator merges a PR when it judges it ready
   (gate green, review clean); no merge word from the user is needed at the
   start. An orchestrator cannot pass the user's approval on for them, so a
   missing rule stalls the run.

3. Grill, one question at a time with a recommendation, glossary and ADRs
   written as they land (`domain-modeling`).

4. Output: a dated design doc with numbered decisions and a PR-sized,
   dependency-ordered build plan with a model per item. On the user's own
   repo (owner in `LEGWORK_OWN_OWNERS`), it lives in the repo's ideas or
   specs folder and goes up as a docs-only pull request. On a shared repo
   (any owner not in `LEGWORK_OWN_OWNERS`), it stays local under
   `.legwork/` (gitignored) with any ADR or `CONTEXT.md`, and no pull
   request is opened for it; the orchestrator's PRs carry code, tests and
   the repo's normal docs only.

5. Tracker: a `## Roadmap N` section (newest first, one item per PR, run
   rules, what needs the user) and a `## Next prompt` of
   `/fanout <project> --merge`, then wrap (`legwork-tracker`) and commit
   the legwork repo. The orchestrator runs on Opus at high effort
   (`Model: opus`, `Effort: high`); each item names the model that fits
   it for the worker that builds it.

   Mark any item that changes the user's machine outside the repo
   (installing live skill copies, loading a launch agent or scheduled task,
   editing settings, starting long-lived sessions) as orchestrator-only,
   and list it under "Needs the user": the permission check treats it as
   persistence and wants the user's own words in the orchestrator's
   session, and a worker will rightly not accept that approval second-hand.
   The next prompt asks for that word at the start.

6. Tell the user it is queued. The switchboard (the Claude Code pane
   `switchboard`) starts the run when the user says "run <project>": it
   opens or reuses an `orch-<project>` pane, sends `/pickup <project>` and
   "run as written" with their merge authorisation, and watches. With
   `LEGWORK_SWITCHBOARD=manual`, the user opens that pane and pastes the
   next prompt themselves.
