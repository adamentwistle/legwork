---
description: Run the Bugbot review loop on a pull request until it is clean. Fix, push, re-trigger, max three rounds.
---

Bugbot loop for pull request: $ARGUMENTS (a PR number, or blank to use the
PR for the current branch via `gh pr view --json number -q .number`, then
optionally `--hold-reviewers`). With `--hold-reviewers`, request none of the
after-the-bot reviewers below: the caller's own gate has not passed yet,
and it requests them once it does. Say in the final reply that they were
held.

Bugbot is Cursor's reviewer. It posts a review as login `cursor`; findings
arrive as inline review comments, a clean run says "found no new issues".
The poll is a script, not a hand-rolled sleep loop, and it runs in the
foreground. Never use Monitor or a background task for this; they have
missed verdicts before. The Bash tool stops a foreground call at 10
minutes, so each wait is at most 540 seconds and each Bash call gets
`timeout: 600000`. A round waits up to 20 minutes in all: on `TIMEOUT`, wait
again with `--after <the "after" it printed> --timeout 540` (no
`--trigger`), until 20 minutes have passed since that time.

The script is `{{LEGWORK_BIN}}/bugbot-wait` (the installer wrote the full
command; run it as written). It only waits on repos whose owner is in `LEGWORK_BUGBOT_OWNERS`
(the legwork `config`, or the environment). The house rules are the list
items of `house-rules.md` in the legwork repo ($LEGWORK_DIR, else
~/legwork), except those in its `Pull request reviews` section; if it does
not exist, say so in the final reply. Its
`Pull request reviews` section, if any, says per repo or per owner who
reviews pull requests and when; a repo's item beats its owner's. If it
names nothing for this repo, request nobody and leave reviews to the user.

Steps, per round (max 3 rounds):
1. If this is round 1 and Bugbot has not yet run on the current head
   commit, trigger and wait in one call:
   `{{LEGWORK_BIN}}/bugbot-wait <pr> --trigger --timeout 540`
   Otherwise, if a verdict may already exist for the current head, run
   `{{LEGWORK_BIN}}/bugbot-wait <pr> --after <ISO time of the last push> --timeout 60` first.
2. Read the JSON it prints.
   - `CLEAR`: stop. Report the review time and the commit it reviewed.
     Then, unless held, request the reviewers that section names for this
     repo for after the review bot is clear, those not already requested:
     `gh pr edit <pr> --add-reviewer <logins>`, never the PR's author.
   - `TIMEOUT`: wait again as above while the round has time left. After 20
     minutes, stop. Report that Bugbot did not answer; do not retrigger blindly.
   - `NO_BUGBOT`: stop. The repo's owner has no Bugbot (it is not in
     `LEGWORK_BUGBOT_OWNERS`). Nothing was posted. Report "no Bugbot on
     <owner>" and do not wait, retrigger or post a "bugbot run" comment by hand.
     There is no bot to wait for, so request the after-the-bot reviewers
     once the PR is otherwise ready: now, as for `CLEAR`, unless held.
   - `FINDINGS`: go to step 3.
3. For each item in `comments`, open the file at the line, decide whether
   the finding is real. Fix the real ones. For a finding you reject, reply
   on that review comment with one sentence saying why (as me, plain text,
   following the house rules). Do not argue in the PR description.
4. Run the repo's gate if it has one (`scripts/pr-gate.sh`, else the test
   command in the README). Commit with a plain message that follows the
   house rules. Push the branch. Never push to main.
5. Next round: `{{LEGWORK_BIN}}/bugbot-wait <pr> --trigger --timeout 540`.

Findings still open or a `TIMEOUT`: request none of the after-the-bot
reviewers; the review bot is not clear.

After the loop, reply with: final state (CLEAR, FINDINGS left, TIMEOUT or
NO_BUGBOT), rounds used, what was fixed, each rejected finding with its
reason, and the reviewers requested (or none). Findings still open after
round 3 are listed, not fixed.
