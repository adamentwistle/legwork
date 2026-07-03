---
description: Vision discovery for a project. Captures the standing brief that lets autonomous sessions act in your place, and optionally opts the project into the runner loop.
---

Follow the legwork-tracker skill, Vision and autonomy section.

Project: $ARGUMENTS (if blank, ask which project this is for).

Steps:
1. Resolve the legwork repo: $LEGWORK_DIR if set, otherwise ~/legwork.
   If it has a remote, run `git pull --rebase` there first.
2. Read the project file, its full log, and the target repo's README or
   PROJECT.md when one exists. Draft a candidate Vision yourself before
   asking anything: the interview corrects a draft, it does not start
   from a blank page.
3. Interview me briefly (a handful of sharp questions, not a form) to pin
   down the five parts of the Vision:
   - North star: the finished state in one sentence.
   - Done means: two to four concrete project-level completion criteria.
   - Guardrails: what sessions must never do here (scope, spend, deps, style).
   - Escalate when: decisions that always come back to me.
   - Taste: preferences that make the work feel right.
4. Write the `## Vision` section into the project file, between the
   frontmatter and `## Next prompt`. Replace any existing Vision wholesale;
   the Vision is current truth, not a log.
5. Ask whether to enable autonomy, stating the deal plainly: setting
   `autonomy: loop` means the runner fires this project's queued prompts as
   unattended sessions in that repo. Those sessions can edit files and run
   git, capped per day; any other command is denied unless that repo's own
   .claude/settings.json allow rules grant it. Set the frontmatter key only
   on an explicit yes. On a yes, also offer to add the repo's test and build
   commands to its allow rules, so unattended sessions can verify their own
   work; list the exact commands and let me approve them.
6. Check the Next prompt still serves the Vision; tweak it if not.
7. Log one line: `- YYYY-MM-DD: Vision captured. Autonomy <enabled|left manual>.`
8. Rebuild the dashboard, commit, and push when a remote exists (degrade
   exactly as the legwork-tracker skill describes: no remote, no push;
   `/projects/` still gitignored, no commit). Confirm in two lines.
