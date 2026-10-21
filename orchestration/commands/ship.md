---
description: Land the current work. Commit, push the branch, open or refresh the pull request, run the Bugbot loop until clean, then /wrap.
---

Ship what is in the working tree, then close the session.
Project for the tracker: $ARGUMENTS if given, otherwise infer it from the repo.
A `--hold-reviewers` in $ARGUMENTS is not part of the project: it goes on
to /bugbot in step 6, for a caller whose own gate has not passed yet.

The legwork repo is $LEGWORK_DIR if set, otherwise ~/legwork. Rules that
apply to every step: every list item of its `house-rules.md` (authorship,
attribution and style), except those in its `Pull request reviews` section.
If that file does not exist, say so in the final reply. That section, if
any, says per repo or per owner who reviews pull requests and when; a
repo's item beats its owner's.
Work lands on a branch through a pull request, never by pushing to main.

Steps:
1. `git status --short` and `git diff --stat`. Stage only the files this
   session changed on purpose; never `git add -A`. Anything unexpected
   stays unstaged and is named in the final reply.
2. If the current branch is `main`, `master` or `release`: stop and say so.
   Do not create a branch and move the work silently; ask which branch name
   to use.
3. Commit with an honest message describing what changed and why. Plain
   message, following the house rules.
4. Push to the branch's upstream (`git push -u origin HEAD` if none). If the
   push would need `--force`, stop and ask.
5. Pull request. If `gh pr view` finds none for this branch, draft the body
   with the skill named by `LEGWORK_PR_SKILL` in the legwork `config`, when
   set, run in full; otherwise TL;DR first, ticket IDs linked, every
   Verification line marked OBSERVED, STALE or NOT RUN. Open it with
   `gh pr create`. Then request the reviewers that section names for this
   repo when the PR opens: `gh pr edit <n> --add-reviewer <logins>`,
   never the PR's author. Those it names for after the review bot is clear
   wait for step 6. If it names nothing for this repo, request nobody and
   leave reviews to the user. If a PR exists, update the body only if the
   change set moved beyond what it describes.
6. Run /bugbot for that PR, with `--hold-reviewers` if this run was given
   it. It triggers Bugbot, waits, fixes real findings, pushes, and
   retriggers, up to three rounds. When it ends clear, it requests the
   reviewers the house rules name for after the review bot, unless held.
7. Run /wrap for the project. The log entry names the PR and the Bugbot
   state.
8. Reply in three lines: PR URL and branch; Bugbot state and rounds, and
   the reviewers requested (or none); what /wrap minted as the next prompt.
