---
description: Quick update to a project without a work session. Notes, status changes, prompt tweaks.
---

Follow the legwork-tracker skill, Lifecycle section, to apply an update.

Update: $ARGUMENTS

Steps:
1. Resolve the legwork repo: $LEGWORK_DIR if set, otherwise ~/legwork.
2. Work out which project this refers to and what kind of update it is:
   a note for the log, a status change, an edit to the description or
   frontmatter, or a tweak to the next prompt.
3. Apply it. Notes are prepended to the log with today's date. Prompt
   tweaks replace the fenced block but keep the prompt shape rules.
   Never rewrite old log entries.
4. Rebuild the dashboard and confirm in one line.
