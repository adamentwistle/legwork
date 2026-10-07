---
description: Run the Bugbot review loop on a pull request until it is clean. Fix, push, re-trigger, max three rounds.
---

Bugbot loop for pull request: $ARGUMENTS (a PR number, or blank to use the
PR for the current branch via `gh pr view --json number -q .number`).

Bugbot is Cursor's reviewer. It posts a review as login `cursor`; findings
arrive as inline review comments, a clean run says "found no new issues".
The poll is a script, not a hand-rolled sleep loop, and it runs in the
foreground. Never use Monitor or a background task for this; they have
missed verdicts before.

The script is `{{LEGWORK_BIN}}/bugbot-wait` (the installer wrote the full
command; run it as written). It only waits on repos whose owner is in `LEGWORK_BUGBOT_OWNERS`
(the legwork `config`, or the environment). The house rules are the list
items of `house-rules.md` in the legwork repo ($LEGWORK_DIR, else
~/legwork); if it does not exist, say so in the final reply.

Steps, per round (max 3 rounds):
1. If this is round 1 and Bugbot has not yet run on the current head
   commit, trigger and wait in one call:
   `{{LEGWORK_BIN}}/bugbot-wait <pr> --trigger --timeout 1200`
   Otherwise, if a verdict may already exist for the current head, run
   `{{LEGWORK_BIN}}/bugbot-wait <pr> --after <ISO time of the last push> --timeout 60` first.
2. Read the JSON it prints.
   - `CLEAR`: stop. Report the review time and the commit it reviewed.
   - `TIMEOUT`: stop. Report that Bugbot did not answer; do not retrigger blindly.
   - `NO_BUGBOT`: stop. The repo's owner has no Bugbot (it is not in
     `LEGWORK_BUGBOT_OWNERS`). Nothing was posted. Report "no Bugbot on
     <owner>" and do not wait, retrigger or post a "bugbot run" comment by hand.
   - `FINDINGS`: go to step 3.
3. For each item in `comments`, open the file at the line, decide whether
   the finding is real. Fix the real ones. For a finding you reject, reply
   on that review comment with one sentence saying why (as me, plain text,
   following the house rules). Do not argue in the PR description.
4. Run the repo's gate if it has one (`scripts/pr-gate.sh`, else the test
   command in the README). Commit with a plain message that follows the
   house rules. Push the branch. Never push to main.
5. Next round: `{{LEGWORK_BIN}}/bugbot-wait <pr> --trigger --timeout 1200`.

After the loop, reply with: final state (CLEAR, FINDINGS left, TIMEOUT or
NO_BUGBOT), rounds used, what was fixed, and each rejected finding with its reason.
Findings still open after round 3 are listed, not fixed.
