---
description: Land the current work. Commit, push the branch, open or refresh the pull request, run the Bugbot loop until clean, then /wrap.
---

Ship what is in the working tree, then close the session.
Project for the tracker: $ARGUMENTS if given, otherwise infer it from the repo.

The legwork repo is $LEGWORK_DIR if set, otherwise ~/legwork. Rules that
apply to every step: every list item of its `house-rules.md` (authorship,
attribution and style). If that file does not exist, say so in the final
reply. Work lands on a branch through a pull request, never by pushing to
main.

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
   `gh pr create`. If one exists, update the body only if the change
   set moved beyond what it describes.
6. Run /bugbot for that PR. It triggers Bugbot, waits, fixes real findings,
   pushes, and retriggers, up to three rounds.
7. Run /wrap for the project. The log entry names the PR and the Bugbot
   state.
8. Reply in three lines: PR URL and branch; Bugbot state and rounds;
   what /wrap minted as the next prompt.
