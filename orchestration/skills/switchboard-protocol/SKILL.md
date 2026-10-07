---
name: switchboard-protocol
description: Use when running as a project orchestrator that reports to the legwork switchboard, such as a long unattended run, a /go or /fanout session in a pane named orch-<project>, or any prompt that says to report to the switchboard.
---

# Switchboard protocol

You run one project for hours while the user works from a single
switchboard: a Hermes thread, or the user reading the file by hand. The
switchboard reads what you write to the switchboard file and relays answers
back into your pane. Decide what you can, and send up only what changes
direction.

## Setup, once per session

- `<legwork>` below is the legwork repo: `$LEGWORK_DIR` if set, otherwise
  `~/legwork`. Its settings are KEY=VALUE lines in `<legwork>/config`; a
  real environment variable wins over the file. Read these once:
  - `LEGWORK_SWITCHBOARD`: `hermes` (a Hermes thread relays) or `manual`
    (the user relays by hand). Unset means `hermes`.
  - `LEGWORK_OWN_OWNERS`: the GitHub users or orgs whose repos are the
    user's own. Every other owner is a shared repo.
  - `LEGWORK_BUGBOT_OWNERS`: the owners that have Cursor Bugbot installed.
  - `LEGWORK_LINEAR_TEAM` and `LEGWORK_LINEAR_TEAM_KEY`: the Linear team
    and its issue key. Unset means no Linear rules.
  - `LEGWORK_UI_SKILL`: the skill that verifies a UI journey in a browser.
    Unset means Playwright.
- Your session is named `orch-<project>`, where `<project>` is the legwork
  slug. The user or the switchboard sets it with `/rename`, and inside
  Herdr it shows as your pane title. Inside Herdr, if `herdr pane current`
  shows a different title, write an `UPDATE` saying so.
- The switchboard file is `<repo>/.legwork/switchboard.md`. Create it if
  missing. The repo must gitignore `.legwork/`.
- Read `CONTEXT.md`, any ADRs and the project's `## Vision` before deciding
  anything. They are what you decide from.
- If the start prompt says you may merge pull requests that pass the gate,
  that covers every merge in this run. Do not escalate merges.

## Decide or escalate

Decide it yourself, and log it as `DECIDED`, unless one of these is true.
If one is, write a `DECISION` entry:

- It has knock-on effects: a public interface, a data shape, another
  project, cost, an external system, or something hard to undo.
- It changes scope, the plan's direction, or the Vision's "Done means".
- The Vision's "Escalate when" names it.
- You cannot pick between the options on the evidence you have.
- It needs the user's hands: a step, an approval, a reinstall, a merge.
  That never goes inside an `UPDATE` or `DONE`, because the switchboard
  only surfaces `DECISION`s.

Everything else is yours: implementation choices, ordering, library use
inside the plan, and fixes.

After writing a `DECISION`, keep working on anything that does not depend
on it. Stop only when nothing is left that does not depend on it.

## Entry format

Append to the switchboard file. Newest last. The id is `<project>-<n>`.
Stamp every entry with the output of `date '+%Y-%m-%d %H:%M'`, run at the
moment you write the entry. Never compose or estimate a time from memory.

```
## <id> DECISION <YYYY-MM-DD HH:MM>
Attempted: <what you tried or looked at>
Uncertain: <the one thing you cannot settle>
Options: A) ... B) ... C) ...
Recommendation: <letter and why, one line>
Blocks: <what waits on this>
Meanwhile: <what you are doing instead>

## <id> UPDATE <time>
<one or two lines: milestone reached, plan changed, run stalled>

## <id> DECIDED <time>
<what, why, the alternative you rejected; one line>

## <id> CYCLE <time>
Resume: <exact command, usually /go <project> or /fanout <project> --resume>

## <id> DONE <time>
<the /fanout wrap list: PRs, review state, skipped, blocked, unproven>
```

A `DECISION` that supersedes an earlier one carries `Replaces <id>` on its
first line. Writing it closes the earlier `DECISION`; do not also answer
or decide the earlier one separately.

Answers arrive in your pane as `SWITCHBOARD ANSWER <id>: <answer>`. Act on
them. Only the switchboard writes the `ANSWERED` line; you never write one,
even when the user answers in the pane directly instead of through the
switchboard. In that case, write a `DECIDED` entry quoting what the user
said.

The one exception is `LEGWORK_SWITCHBOARD=manual`. Then there is no relay,
and an answer the user types into your pane, with or without the
`SWITCHBOARD ANSWER` prefix, is the switchboard answer. It answers the id
it names, else the only open `DECISION`; if several are open and it names
none, ask which. A question or a correction is not an answer: reply to it
and leave the `DECISION` open. For an answer, append
`ANSWERED <id>: <their words verbatim> (<output of date '+%F %H:%M'>)`
to the end of the file yourself, then act on it.

## Context cycling

Cycle once context passes 300,000 tokens or 60% of the window, whichever
comes first. On a 1M window that is 30%, on a 200k window 60%. Quality
drops long before a big window fills. Finish the current step and find a
clean pause, meaning a task landed and nothing is half-edited. Then:

1. Run `/wrap <project>` so the next prompt carries the exact state. The
   minted prompt's first line must be `Follow the switchboard-protocol
   skill.` so the next session keeps reporting.
2. Write a `CYCLE` entry with the resume command.
3. Stop. The switchboard sends `/clear`, `/rename orch-<project>` and the
   resume command. With `LEGWORK_SWITCHBOARD=manual`, the user does.

## Usage budget

Read `budget:` in the frontmatter of `projects/<project>.md` in the legwork
repo. Check usage with `{{LEGWORK_BIN}}/usage-guard --check` at
every cycle and after every pull request lands. If it prints `no usable
usage cache`, there are no figures this time: write one `UPDATE` saying the
budget cannot be enforced, do not repeat that `UPDATE`, and keep checking
at every cycle, because a cache can come back.

- `budget: <N>`: once the weekly all-models or Fable figure passes N%, write
  a `DECISION`: A) land what is open and stop, B) carry on, C) stop now.
- `budget: off`, or no `budget:` line: no threshold.
- Whatever the setting, at 99% on either weekly figure: finish the current
  step, write a `DECISION`, and start nothing new until it is answered.
- When a `DECISION` is a budget or spend question and the user has not
  answered by the time work would stall, take the least-spend reversible
  option, mark the `DECIDED` entry "provisional, taken while the user was
  unanswered", and carry on. Credentials, security defaults and anything
  outward-facing still wait for an answer.

## Running /fanout under this protocol

Where the fanout skill and this protocol disagree, this protocol wins:

- **Context:** use `CYCLE` with `Resume: /fanout <project> --resume` instead
  of fanout's fresh-pane handoff. Update `.legwork/fanout-state.md` first.
- **Items that need a human:** write a `DECISION` instead of only listing
  them in the final report.
- **Bugbot:** skip `/bugbot` on repos whose owner is not in
  `LEGWORK_BUGBOT_OWNERS`. Step 3 of the quality gate replaces it there.

## Linear

Skip this section when `LEGWORK_LINEAR_TEAM` is unset.

For each PR or other change on the project, create an issue on the
`LEGWORK_LINEAR_TEAM` team, or reuse the roadmap item's issue if it already
has one: title is the PR title, description is one line on what and why
plus the PR link, project is the repo's project when one exists. Do this at
dispatch time, before the PR opens.

Move it to In Progress when the PR opens; to Done when it merges (or a
"Delivered" style final state when the PR is handed to a maintainer
unmerged, if the team has one, otherwise leave it In Review). Always add
a comment saying what landed and the PR number when you move it.

Issue ids (`<LEGWORK_LINEAR_TEAM_KEY>-NN`) never appear in a PR title,
body, commit or doc on a shared repo; only on the user's own repos (owner
in `LEGWORK_OWN_OWNERS`) and in the tracker. Linear writes stay inside
that one team throughout.

## Quality gate, for every feature

1. **Grill first.** Run `/grill-with-docs` (from the mattpocock-skills
   plugin) on any new feature before planning it. Without that skill,
   interview yourself the same way: one question at a time, each answered
   from the docs and the Vision. On Fable, Opus 5.5 or gpt-6-astra, answer
   the questions yourself from the docs and the Vision, and record the
   answers in `CONTEXT.md` or an ADR. On any other model, send steering
   questions up as `DECISION`s.
2. **Test first.** Write the failing test and commit it before the code.
   It must fail on the base commit for the reason the feature fixes. For a
   bug report, the first test is the reporter's exact phrasing or steps.
   Any state the change adds gets one test per re-dispatch path (recovery,
   reload, retry, cancel with a call in flight, reopen).
3. **Adversarial review before ready.** Have a second model review the
   diff: the `codex` agent, when you have one. Verify each finding with a
   separate subagent before fixing it. Fix the confirmed ones and note the
   rejected ones in the PR body. Each confirmed finding is its own commit,
   regression test committed failing first. When no second model is
   available (none installed, spend cap, outage), an Opus adversarial
   review with each finding verified by a separate subagent stands in.
   Record it as a `DECIDED`, not a `DECISION`, and name the kind of review
   it was in the PR body.
4. **UI changes:** verify the journey in a real browser, with the
   `LEGWORK_UI_SKILL` skill when that is set, else Playwright.
5. **Tests stay out of real user state.** Run them with `HOME` pointed at a
   temporary folder. Anything that touches a keychain, credential store or
   app config needs a test proving it used a scratch copy, before its first
   run. A brief saying so is not enough.
6. **Repos whose owner is in `LEGWORK_BUGBOT_OWNERS`:** also run `/bugbot`.
   Other repos have no Bugbot, so step 3 is the gate there.
7. **Renames and ID swaps** sweep every reference site: constants, tests,
   user-visible copy, docs and the comments that describe the old order.

A PR is ready only when all the steps that apply have passed, each marked
OBSERVED in the PR body, and the body names what was NOT RUN and why,
including steps only a maintainer can run.
