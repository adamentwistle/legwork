## legwork-150 UPDATE 2026-09-25 19:52
Merged 325 (the three atomic writes are now one helper; every file keeps its mode; a long-name fault found in review was fixed tests first), 321 (the prove triage and its fixed checks) and 324 (a marked orphan process now stops wherever it lives; live proof 10 of 11, the cross-model review not run because of the spend cap). Still running: the plugin cache row and the matcher move. The review findings on the orphan fix are still being checked.

## legwork-151 DECISION 2026-09-25 19:59
Attempted: plugin cache option A is built as 327 and is in its gate. A plugin that is a plain program or script now starts inside the sandbox, proven live. npx and uvx plugins, which is most of the catalogue, still do not start: both write their cache on every start (npm: EPERM on ~/.npm/_cacache/tmp; uv: its cache under ~/.cache/uv), and offline mode does not help.
Uncertain: whether to let a sandboxed plugin write a cache. Your own npx and uvx run code from ~/.npm and ~/.cache/uv outside any sandbox, so opening those for writing would let a sandboxed process plant code that later runs as you. The worker refused to do that, rightly.
Options: A) Give each sandboxed plugin a cache of its own (npm_config_cache and UV_CACHE_DIR pointed into a private folder), and add the registry hosts to its allow list. Cost: the plugin can plant code in its own cache, which then runs with that plugin's token, though still inside the sandbox. B) Leave npx and uvx plugins off inside the sandbox for now, with the plugin page saying so, and solve it with option B from legwork-146 (start plugins outside the sandbox). C) Open your real caches for writing. Not recommended.
Recommendation: B for v0.1.0, as plain copy on the plugin page. A private cache gives the token to code the sandboxed process can write, which is the original threat again. Option B removes the problem rather than fencing it.
Blocks: npx and uvx plugins inside the sandbox only. 327 merges either way once its gate and review pass.
Meanwhile: 327's review, then the code health rows and the release.

## legwork-152 CYCLE 2026-09-25 20:01
Context cycle after 15 merges. Still running across the cycle: the row 14 worker (pane ch-summary) and 327's gate (pane w5Y:p1, the release pane). Open for you: legwork-151 (npx and uvx plugins in the sandbox, recommendation B).
Resume: /fanout legwork --resume
ANSWERED legwork-151: B (2026-09-25 20:10)

## legwork-163 UPDATE 2026-09-26 01:22
Merged 332. The avatar timing check now measures what the disk cache saves: 12 ms cached against about 620 ms fresh. It also fixed a real bug where the cache file could be read half-written. Filed two low-priority issues: with animation on, the cache saves nothing; a symlinked attachments folder is still trusted. The final v0.1.0 prove run is going on 06c6f2b.

## legwork-164 DECISION 2026-09-26 01:38
Attempted: v0.1.0 on main 06c6f2b, which includes 327, 330, 331 and 332. The gate passes. The full prove run gives 68 OBSERVED, 0 FAILED, 15 NOT RUN, each NOT RUN with its reason (native dialogs, microphone, screen reader, real packages). The build is signed, notarised and stapled. release-check.sh passes. v0.1.0 is tagged locally only (annotated, on 06c6f2b); nothing is pushed.
Zip: <scratchpad>/release-v0.1.0.zip (11.8 MB).
Uncertain: one cosmetic flaw. The bundle's Info.plist says 1.0.0, the framework default. The app itself carries v0.1.0, which is what Check for Updates reads. Finder's Get Info shows 1.0.0.
Options: A) Publish now: I push the tag and run gh release create v0.1.0 --prerelease with the zip. B) Fix the plist version first, then re-tag on the new commit, rerun the prove run and rebuild, about 90 minutes. C) Hold.
Recommendation: A. The flaw is cosmetic, and the update path reads the right version. The plist fix lands before v0.1.1.
Blocks: publishing v0.1.0, then 323 (your legwork-158 A) and v0.1.1.
Meanwhile: merging main into 323 and gating it, ready to merge the moment the tag is out.

## legwork-165 CYCLE 2026-09-26 01:58
Context cycle after the v0.1.0 build. Still running across the cycle: the 323 merge-review fix (pane seam-fix). Open for you: legwork-164 (publish v0.1.0, recommendation A).
Resume: /fanout legwork --resume
ANSWERED legwork-164: A (2026-09-26 07:00)

## legwork-145 DECISION 2026-09-25 18:30
Attempted: the full prove run for v0.1.0 on main 1a03742 had 26 FAILED lines. The triage (PR 321) sorts them: 19 were the machine's load, 3 were stale checks (now fixed), and 4 are one real defect, the plugin cache issue.
Uncertain: the release checklist wants a prove report with no FAILED line, and the plugin cache issue keeps one check red until its design is decided (legwork-146).
Options: A) Release v0.1.0 with the plugin cache issue as a written known gap. Every other prove line must come back OBSERVED on a rerun first. B) Hold v0.1.0 until the plugin cache issue is fixed.
Recommendation: A. It is a prerelease for you alone, the defect is already on main, and B puts the release behind a sandbox design choice.
Blocks: the v0.1.0 release DECISION (commit and zip).
Meanwhile: merging the reviewed fixes, then rerunning the prove lanes. Load on this machine has been high all afternoon: my workers plus another project's.

## legwork-146 DECISION 2026-09-25 18:30
Attempted: the plugin cache issue. A plugin runs as a child of the sandboxed CLI, so it inherits the sandbox profile. That profile denies the plugin's own files: home (npx and uvx caches), the interpreter's framework folder and its package folder.
Uncertain: how much the sandbox should open for a plugin. It touches what a sandboxed process can read and reach.
Options: A) For each plugin enabled inside the sandbox, open read-only exactly its command, the path arguments, its interpreter's framework folder and its package folder. B) Start plugins outside the sandbox altogether, through a gateway that holds their tokens.
Recommendation: A for now (small and testable; the extra reads are package code, not your data), with B on the roadmap if you want the tighter line later.
Blocks: the plugin cache fix, and a fully green prove report.
Meanwhile: nothing waits on this except that one prove line.
ANSWERED legwork-145: A, add the plugin cache issue to the roadmap (2026-09-25 18:52)
ANSWERED legwork-146: A now, B on roadmap; gateway noted (2026-09-25 18:52)

