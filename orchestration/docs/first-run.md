# First run

The first real run. One project, a few hours, planned with you and then
left to run.

## 1. Pick the project

- A legwork project in one of your own repos. If it has no Bugbot, the
  adversarial review is the gate, which is the part most worth testing.
- A feature worth two to four pull requests. Small enough to finish in an
  afternoon, big enough to need at least one context cycle.
- Nothing customer-facing or shared, so a bad decision costs a revert and
  nothing else.

## 2. Prep, about 5 minutes

1. Open `projects/<project>.md` in the legwork repo and check:
   - `repo:` points at the right folder
   - `account:` is set, if you run more than one Claude account
   - there's a `## Vision` with "Escalate when" filled in. If not, run `/vision <project>` first. That section is what the orchestrator decides from, so it matters more than anything else here.
2. Optional: add `budget: <N>` to the frontmatter to get a `DECISION` when either weekly figure passes N%. `budget: off`, or no line, means no threshold. At 99% it always stops and asks, whatever the setting. This needs the usage cache; without one the budget cannot be enforced.
3. Check the repo's `.gitignore` has `.legwork/`.
4. Write down the two weekly figures from the Claude status line.

## 3. Planning session, with you, on your strongest model

1. New Herdr pane (or any terminal):
   ```
   cd <repo> && claude
   ```
2. `/pickup <project>`
3. `/grill-with-docs` (from the mattpocock-skills plugin). When it asks what to grill, pick the feature for the run and scope it to the two to four pull requests. Avoid anything its options flag as hard to undo. Answer everything. This fills in `CONTEXT.md` and the ADRs that the long run decides from, so this is where to spend the time.
4. Ask for the plan:
   ```
   Write the plan as roadmap items that /fanout will pick up: one item per pull request, each with its acceptance check, in dependency order. Say which items are parallel.
   ```
5. `/wrap <project>`. Before it finishes, make sure the minted next prompt starts with `Follow the switchboard-protocol skill.`
6. Close the pane.

## 4. Start the orchestrator

1. New Herdr pane (or any terminal):
   ```
   cd <repo> && claude
   ```
2. `/model`, pick Opus. It's strong enough to answer its own grilling questions, and it keeps the long run off your scarcest weekly limit.
3. `/rename orch-<project>`
4. Paste:
   ```
   Follow the switchboard-protocol skill for project <project>. Then run the fanout skill for <project>. Report to the switchboard as you go. You may merge pull requests that pass the gate.
   ```
   Leave the last sentence out if you want to merge by hand. Without it, the auto-mode classifier stops every merge and each one comes to you as a `DECISION`.
5. Leave it alone from here.

## 5. Start the switchboard

With Hermes:

1. New Herdr pane (or any terminal), any folder: `hermes -p <profile>`
2. Type:
   ```
   Use the switchboard skill. Watch orch-<project>.
   ```
3. While it runs:
   - Answer decisions with a letter, or in your own words.
   - To talk to Hermes while it's watching, interrupt it, then say `check in` or ask what you want.
   - Stay out of the orchestrator pane unless Hermes flags it as blocked.

By hand: run `orchestration/bin/switchboard-wait --timeout 1800 <repo>:<pane>`
(or `<repo>:none` without Herdr) in a spare terminal. It returns when there
is something for you. Read the entry in `.legwork/switchboard.md`, type
your answer into the orchestrator pane, and start the wait again. On a
`CYCLE`, type `/clear`, `/rename orch-<project>` and the `Resume:` line
into the pane. `--status <repo>:none` always shows a trailing `CYCLE` or
`DONE`, even after the plain wait has reported it once.

## 6. When it finishes

The orchestrator writes a `DONE` entry, and the switchboard will show it at
the next check-in.

1. Write down the two weekly figures again, and the run time.
2. Close the orchestrator pane. `herdr worktree list` should show nothing left over from the run.

## 7. Review

Go through these before widening it to more projects:

- **Escalation:** read the `DECIDED` entries in `.legwork/switchboard.md`. Did anything go through that you'd have wanted to see? Did anything come up that it should have decided?
- **Cycling:** did each `CYCLE` land at a clean point? Did the session after it pick up the right state?
- **Tests first:** in `git log`, is each test commit before the code it tests?
- **Adversarial review:** does each pull request body show the second-model review, with confirmed and rejected findings marked OBSERVED?
- **UI:** if the feature has UI, was the journey checked in a real browser?
- **Usage:** each weekly figure used per hour of run.
- **Hermes learning:** diff the installed Hermes skill against `orchestration/hermes/switchboard/SKILL.md` and bring back anything worth keeping.

## If something goes wrong

- **The orchestrator stops without a `CYCLE` or `DONE` entry:** read its pane, fix the cause, then run `/go <project>` in it.
- **The switchboard relays to the wrong pane:** stop it, then run `herdr agent list` and check the titles. Two panes titled `orch-<project>` will confuse it.
- **The run goes off course:** answer the next `DECISION` with a correction, or type into the orchestrator pane directly. Note what the Vision was missing.
