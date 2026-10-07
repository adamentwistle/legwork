---
description: Run a project's queued legwork prompt exactly as written, no re-briefing.
---

Run the next prompt for: $ARGUMENTS

Steps:
1. Resolve the legwork repo: $LEGWORK_DIR if set, otherwise ~/legwork.
2. Read projects/$ARGUMENTS.md there. Take the fenced block under
   `## Next prompt` verbatim.
3. If it opens with `Human action, not a Claude session.` or
   `DECISION NEEDED`, stop and show it to me instead of running it.
4. If it has a `Model:` line and that is not the model this session is on,
   say so in one line, then continue on the current model anyway.
5. Execute the prompt as written. Do not re-brief me, do not ask whether to
   adjust it, do not narrow or widen it. The prompt is the spec.
6. Its own final step is /wrap; do that when the work is done.
