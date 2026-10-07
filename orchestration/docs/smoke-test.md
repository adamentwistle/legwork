# Smoke test

Checks the plumbing on a cheap model before spending a strong one on a real
run. Takes about 15 minutes.

You need two Herdr panes, or two terminals without Herdr. Pane 1 is Claude
Code, playing the orchestrator. Pane 2 is Hermes, playing the switchboard.

Relaying by hand (`LEGWORK_SWITCHBOARD=manual`)? Skip pane 2. Type `A`
into pane 1 yourself and check the `ANSWERED` line the orchestrator writes.
For the cycle, type `/clear`, then `/rename orch-smoke`, then the `Resume:`
line from the `CYCLE` entry, into pane 1.

## Pane 1: the orchestrator

1. Make a throwaway git repo that ignores `.legwork/`, then:
   ```
   cd <repo> && claude --model sonnet
   ```
2. Type `/rename orch-smoke`.
3. Paste:
   ```
   Use the switchboard-protocol skill. This is a smoke test, project "smoke". Write one UPDATE, then one DECISION choosing between A) add a line to README.md and B) no change, recommending A. Wait for the answer, act on it, then write a CYCLE entry with Resume: "Read .legwork/switchboard.md and write an UPDATE saying the cycle worked." Do not run /wrap.
   ```

## Pane 2: the switchboard

1. Run `hermes -p <profile>`.
2. Type `Use the switchboard skill. Check in on orch-smoke. Its repo is <repo>.`
3. Answer `A`.
4. When pane 1 is idle, type `Cycle orch-smoke.`

## It passed if

- The switchboard showed the decision as written.
- The answer reached pane 1 and `ANSWERED <id>: A` appeared under the decision.
- After the cycle the pane title was `orch-smoke` again and the new session wrote "cycle worked".

## Known gotcha

`herdr agent prompt --wait` fails with `agent_prompt_stalled` on slash
commands, because `/clear` and `/rename` never show a working state even
though they land. The Hermes skill skips `--wait` for those and checks the
pane's context figure instead.
