---
description: Archive a project to the icebox, guilt-free, with the reason logged.
---

Follow the legwork-tracker skill, Lifecycle section, to shelve a project.

Project and reason: $ARGUMENTS

Steps:
1. Resolve the legwork repo: $LEGWORK_DIR if set, otherwise ~/legwork.
   If it holds a `house-rules.md`, follow it as the skill's House rules
   section says.
2. Set status to icebox. Prepend a log line with today's date and the
   reason, stated plainly. No editorialising, shelving is a valid move.
3. Replace the next prompt with a reopen note: one line on where to pick
   it back up if it ever returns.
4. If I asked for deep archive, move the file to projects/archive/
   instead (the dashboard ignores subfolders).
5. Do what any house rule says to do after /shelve.
6. Rebuild the dashboard and confirm in one line.
