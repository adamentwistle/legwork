## recipe-box-17 DECISION 2026-09-25 19:26
Attempted: report 8 (search spike). The read-only search flag is built and committed on the local branch report/search-spike (76e5b0f6, the build passes). The first run on the second-model reviewer hit its spend cap before it returned anything.
Uncertain: whether you want to sign a subscription seat into the app for this, or wait for the reviewer's cap to reset.
Options: A) You sign a seat into the app (Settings); the worker also blocks the seat-to-API-key fallback in its local build only, so nothing is metered. B) Wait for the cap to reset tomorrow and run report 8 then.
Recommendation: A, because it is the only path that runs today, and the fallback block keeps the no-metered rule safe.
Blocks: report 8 only.
Meanwhile: report 7 is running on the CLI seat directly; the update banner PR is in its final gate run.

## recipe-box-18 UPDATE 2026-09-25 19:30
Report 7 done: .legwork/roadmap-1/reports/compaction-probe.md. On the subscription seat (no metered spend), one compaction kept all 12 planted facts every time; losses come from compacting a summary again.
recipe-box-17 on hold: report 7 ran on a seat through the app's own CLI config, so report 8's "no seat" is being re-checked before you need to act. Answer recipe-box-17 only if it comes back BLOCKED again.

## recipe-box-19 UPDATE 2026-09-25 19:46
PR 282 (update banner in its own row, preparing state shows progress) opened, gate PASS, Bugbot CLEAR on 08654f7d at round 1. Its live check and review predate the last commit, so both are being rerun.

## recipe-box-20 DECISION 2026-09-25 19:46
Replaces recipe-box-17 (no need to answer recipe-box-17).
Attempted: report 8 re-check. The seat IS signed in for the app (auth status against the app's CLI config: logged in). The earlier "no seat" was most likely the startup probe running before the config loaded.
Uncertain: whether report 8 may run on that seat now, with the metered fallback blocked in the local build.
Options: A) Run report 8 now on the seat, fallback blocked. B) Wait for the reviewer's cap and run it there.
Recommendation: A. Nothing is metered, and it unblocks the last report today.
Blocks: report 8 only.
Meanwhile: the banner PR's rerun.

