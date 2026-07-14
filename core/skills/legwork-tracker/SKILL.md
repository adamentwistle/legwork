---
name: legwork-tracker
description: Manage the legwork project queue across its full lifecycle. Use whenever the user runs /wrap, /add, /log, /shelve, /pickup or /vision, says "wrap up", "close out", "log this", "shelve", "reopen", "add a project", "change the status" or "tweak the prompt", or is clearly finishing, starting, updating or retiring work on any project. Every session that produced real work must end with a tracker update.
---

# Legwork tracker

Legwork is a self-updating project queue. Each project is one markdown file
in the legwork repo under `projects/`. The dashboard is generated from those
files. Your job at the end of a session is to update the right file and mint
the next prompt while context is still hot. This is the single habit the
whole system depends on.

The legwork repo lives at `$LEGWORK_DIR` if set, otherwise `~/legwork`.

## File format

Frontmatter holds simple one-line values only:

```
---
name: Widget
category: work          # work | business | personal
status: queued          # queued | running | review | escalated | done | icebox
energy: deep            # deep | medium | light
description: One line that says what this is.
repo: ~/code/widget     # or: none
updated: 2026-06-09
autonomy: loop          # optional; only ever set via /vision, never assumed
account: work           # REQUIRED when category is work; runs under CLAUDE_CONFIG_DIR_WORK
blocked_on: waiting on X     # optional; the precondition the project waits on
---
```

`account` pins which Claude profile the session runs under: `account: <name>`
fires under `CLAUDE_CONFIG_DIR_<NAME>`. **A `category: work` project MUST set
`account: work`.** Without it the session falls back to `CLAUDE_CONFIG_DIR` --
the personal profile -- and would run work code under the personal account with
nothing in the log to say so. The runner refuses to fire a work project that
does not pin `account: work`, and refuses too when the matching
`CLAUDE_CONFIG_DIR_<NAME>` is unset: firing under the wrong identity is worse
than not firing.

`blocked_on` parks a project without iceboxing it: the dashboard shows the
blocker on the card and the runner refuses to fire while it is set. Set it
via /log when a dependency appears; clear it the same way when the blocker
lifts.

Below the frontmatter, two required sections and one optional one:

- `## Vision` (optional) holding the standing brief: North star, Done means,
  Guardrails, Escalate when, Taste. Written by /vision, replaced wholesale
  when re-run, never appended to like a log.
- `## Next prompt` containing one fenced code block with the prompt
- `## Log` containing dated bullets, newest first

## Statuses

- queued: has a ready next prompt, waiting to be fired
- running: a session or external party is actively on it
- review: work finished, awaiting reviewer verdict
- escalated: blocked on a human decision
- done: shipped or complete
- icebox: deliberately shelved, no guilt attached

Icebox is a legitimate destination, not a failure state. When the user
shelves something, move it there cleanly and never editorialise about it.

## Vision and autonomy

The Vision section is what lets a session act while the human is away. Five
bullets: North star (the finished state in one sentence), Done means
(project-level completion criteria), Guardrails (what sessions must never do
here), Escalate when (decisions that always go back to the human), Taste
(preferences that make the work feel right).

`autonomy: loop` in the frontmatter opts the project into the runner
(suite/legwork_runner.py, fired by launchd every five minutes): queued
prompts launch as headless sessions in that repo. Headless sessions get file
edits and git, nothing more by default; the repo's own .claude/settings.json
allow rules grant its test and build commands, and the /vision interview is
where the human adds those. Only the human sets autonomy: loop, through
/vision, after hearing what it means. The runner refuses projects without a
Vision section, so /vision is the single gate into autonomy.

Headless sessions run under the config dir named by `CLAUDE_CONFIG_DIR` when
it is set, so they never inherit whatever account your interactive shell
defaults to; leave it unset to use the default config. A project whose
frontmatter says `account: <name>` fires under `CLAUDE_CONFIG_DIR_<NAME>`
(uppercased) instead, for a dedicated config dir; only the human sets it.

Autonomous sessions follow the same rules as any session, plus two: never
wait for input, and when a decision falls outside the Vision (or touches
money, production deploys, credentials, sending things to people, deleting
data), wrap with status escalated and a DECISION NEEDED brief instead of
guessing. The Telegram loop closes the circuit: the human answers the
letter or replies continue, reply capture queues the project on the remote,
the runner pulls and fires the next session.

When the legwork repo has a remote (`git remote` prints one), every tracker
operation begins with `git pull --rebase` there and ends with `git push`:
n8n and other machines write decisions and minted prompts straight to the
remote, so the local clone can be behind. A remoteless checkout (someone
trying legwork locally) skips the pull and the push: commit locally and
move on, never error on the missing remote.

1. Pull first: `git pull --rebase` in the legwork repo, when it has a
   remote; skip this when `git remote` prints nothing.
2. Identify the project file. If none exists, create a new
   `projects/<kebab>.md` following the File format spec above (frontmatter,
   `## Next prompt`, `## Log`).
3. Prepend a log bullet: `- YYYY-MM-DD: what actually happened, one line.`
   Never rewrite or delete old log entries. The log is append-only history.
4. Set `updated` to today.
5. Set the status that is true right now. If the work is finished and the
   review pipeline is wired, use `review`. If finished and there is no
   pipeline, use `queued` with a fresh prompt, or `done`.
6. Mint the next prompt (rules below).
7. Write a short verification summary to `.legwork/last_test_output.txt`
   in the repo you were working in (the worked-on repo, not the legwork
   repo unless they are the same). List the commands you ran this session
   and their results in a few plain lines. Create `.legwork/` if absent
   and make sure that repo gitignores it (add `.legwork/` to its
   .gitignore). The SessionEnd hook reads this file and forwards it to the
   reviewer as test evidence, so writing it stops false "no test evidence"
   escalations. If you genuinely ran no commands, say so in one line rather
   than leaving stale output from a previous session.
8. Rebuild the dashboard from the legwork repo:
   `python3 core/build_dashboard.py` — on Windows use `python`, as there is
   no python3 there beyond a stub that exits 9009 without running anything.
9. Commit your changes with an honest message, then `git push` when the
   repo has a remote: it is shared with n8n and other machines, so never
   leave local tracker commits unpushed. Two graceful degradations: with no
   remote, stop after the local commit; and if git refuses the add because
   `/projects/` is still gitignored (a fresh clone of the public repo ships
   that way), skip the commit (the file is saved on disk) and point the
   user at SETUP.md's "Make this repo your tracker" step in your reply.

## Minting the next prompt

The next prompt is the most valuable thing you write all session. It is what
lets a cold session, possibly a headless one, resume without the user
reloading context. Rules:

- Assume a cold start. The prompt must point at standing context first:
  "Read the repo's README or PROJECT.md and the last three log entries in
  legwork/projects/<file>."
- One task only, small enough to finish in a single sitting. If the obvious
  next step is big, cut it down and put the rest in the log as a note.
- Include a `Done when:` line with a concrete, verifiable finish line.
- Pick the model for the task with a `Model:` line when it differs from the
  default tier: `Model: haiku` for mechanical chores (renames, dep bumps,
  formatting), `Model: sonnet` for routine build work, `Model: opus` for
  architecture, gnarly debugging, or writing a human will read. Optionally
  add `Effort: low|medium|high|xhigh|max` to match. Omit
  both to run on the account default. The runner strips these lines and
  passes --model/--effort; in manual sessions they are harmless context.
- End with: `Final step: run /wrap to update the tracker and mint the next
  prompt.` This keeps the chain unbroken.
- Keep the whole prompt under 15 lines.
- When the project has a Vision section, the prompt must serve it. Do not
  mint a prompt that drifts from the North star or crosses a Guardrail.
- For projects that are not Claude work (house moves, decisions, calls),
  write the next human action in the same block and open it with
  `Human action, not a Claude session.` The runner never fires these.

## Escalating

When work is blocked on a decision only the user can make:

1. Set status to `escalated`.
2. Replace the Next prompt block with a decision brief, opening with
   `DECISION NEEDED` and containing four parts: Attempted, Uncertain,
   Options (two or three, lettered), Recommendation.
3. Keep it under 15 lines. The user should be able to answer with one letter.

## Lifecycle

The full set of verbs. Each one ends by rebuilding the dashboard.

**Add** (/add). Create `projects/<kebab>.md` from the File format spec
above, fill the frontmatter, write one honest log line, and mint a
real first prompt. Ask the user only for what you cannot infer.
Parallel sessions are the point of this system, so there is no cap on
active projects. Staleness pills on the dashboard are the health signal.

**Vision** (/vision). Read the project file, its log and the target repo's
docs, draft a candidate Vision, then interview the user to correct it.
Write the `## Vision` section between the frontmatter and `## Next prompt`,
replacing any previous one. Offer autonomy explicitly, naming the deal
(unattended sessions that edit files and run git in that repo, a daily cap,
anything more needs that repo's own allowlist); set `autonomy: loop` only on
an explicit yes, and offer to add the repo's test and build commands to its
.claude/settings.json allow rules so unattended sessions can verify their
own work. Log one line either way.

**Log an update** (/log). For news that happened outside a work session:
prepend a dated log bullet, bump `updated`, and adjust the status or next
prompt only if the update changes them. This is also the route for edits:
description changes, frontmatter fixes, status corrections.

**Tweak the prompt** (/log or conversation). Replace the fenced block
under `## Next prompt`, keeping the prompt shape rules. Note the tweak in
the log if it changes what the next session will do.

**Shelve** (/shelve). Set status to `icebox`, log the reason plainly, and
replace the next prompt with a one-line reopen note saying where to pick
it back up. Never editorialise: shelving is a valid move, not a failure.

**Deep archive**. When the icebox itself gets noisy, move retired files
to `projects/archive/`. The dashboard only reads the top level of
`projects/`, so anything in a subfolder disappears from the view while
staying in git history. Reserve this for projects that are properly over.

**Reopen**. Move the file back from `projects/archive/` if needed, set
status to `queued`, log the reopen and why, and mint a fresh prompt. The
old reopen note tells you where to start. Never reuse a stale prompt.

**Done** (/wrap). Set status to `done` with a final log line. Done is
not dead: if the project will live on, mint a standing maintenance prompt
(refactors, tests, docs, dependency bumps, small improvements) so a
finished project can still be picked off the dashboard and improved
autonomously. Open the prompt with `Maintenance pass, not new build.`
Deep archive properly retired projects instead.
