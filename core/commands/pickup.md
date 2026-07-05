---
description: Reload a project's context in 30 seconds before starting work.
---

Give me a re-entry briefing for: $ARGUMENTS

Steps:
1. Resolve the legwork repo: $LEGWORK_DIR if set, otherwise ~/legwork.
2. Read projects/$ARGUMENTS.md there in full.
3. If the frontmatter has a repo path, also read that repo's README or
   PROJECT.md if present.
4. Brief me in at most six lines: what this project is, where it stands,
   the last thing that happened, and the current next prompt.
5. Ask one question only: run the next prompt as written, or adjust it first?
