---
description: Close out this session. Update the legwork tracker and mint the next prompt.
---

Follow the legwork-tracker skill to wrap this session.

Project: $ARGUMENTS (if blank, infer from the repo you are working in).

Steps:
1. Resolve the legwork repo: $LEGWORK_DIR if set, otherwise ~/legwork.
   If it has a remote, run `git pull --rebase` there first: n8n writes
   decisions and minted prompts straight to the remote, so the local
   clone can be behind. No remote? Skip the pull, and the push in step 6.
2. Update the project's file there: last action into the log, set the
   right status. The updated date follows the newest log entry.
3. Mint the next prompt while context is hot. Follow the prompt shape
   rules in the skill exactly.
4. Write a short verification summary (commands run this session and their
   results) to .legwork/last_test_output.txt in the repo you were working
   in, and make sure that repo gitignores .legwork/. The SessionEnd hook
   forwards this to the reviewer as test evidence.
5. Rebuild the dashboard from the legwork repo: `python3
   core/build_dashboard.py` (on Windows use `python` — there is no python3
   there, only a stub that exits 9009).
6. Commit your changes with an honest message and, when a remote exists,
   `git push`: it is shared with n8n, so never leave local tracker commits
   unpushed. If git refuses the add because `/projects/` is gitignored (a
   fresh clone ships that way), skip the commit and point me at SETUP.md's
   "Make this repo your tracker" step; the file is saved on disk either way.
7. Reply with a two line confirmation: what was logged, what the next
   prompt is.
