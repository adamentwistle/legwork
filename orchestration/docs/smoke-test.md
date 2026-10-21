# Smoke test

Checks the plumbing on a cheap orchestrator before spending a strong one
on a real run. Takes about 15 minutes. It works the same on a Mac and on
Windows; the only difference is `python` in front of the scripts on
Windows.

You need two Herdr panes. Pane 1 is a throwaway orchestrator. Pane 2 is
the Claude Code switchboard, set up as in
[switchboard-setup.md](switchboard-setup.md), with its allow rules in
place.

Relaying by hand (`LEGWORK_SWITCHBOARD=manual`)? Skip pane 2. Type `A`
into pane 1 yourself and check the `ANSWERED` line the orchestrator writes.
For the cycle, type `/clear`, then `/rename orch-smoke`, then the `Resume:`
line from the `CYCLE` entry, into pane 1.

## Pane 1: the orchestrator

1. Make a throwaway git repo that ignores `.legwork/`, then:
   ```
   cd <repo> && claude --model sonnet
   ```
   Answer the "trust this folder" question if it asks.
2. Type `/rename orch-smoke`.
3. Paste:
   ```
   Use the switchboard-protocol skill. This is a smoke test, project "smoke". Write one UPDATE, then one DECISION choosing between A) add a line to README.md and B) no change, recommending A. Wait for the answer, act on it, then write a CYCLE entry with Resume: "Read .legwork/switchboard.md, write an UPDATE saying the cycle worked, then write a DONE entry." Do not run /wrap.
   ```

## Pane 2: the switchboard

1. Start the switchboard in the legwork repo as
   [switchboard-setup.md](switchboard-setup.md) says, then type
   `/switchboard smoke`, then `Its repo is <repo>.` (smoke has no tracker
   file to read the repo from).
2. It names its pane `switchboard`, writes `smoke` into its state file,
   switches the health watcher on when it is installed, and starts a
   foreground `switchboard-wait`.
3. When it shows the decision, press Esc, then answer `A`.
4. When pane 1 writes its `CYCLE` and goes idle, the switchboard sends
   `/clear`, `/rename orch-smoke` and the `Resume:` line by itself. If it
   does not, press Esc and type `Cycle orch-smoke.`
5. On the `DONE`, it tells you, takes `smoke` off its state file and,
   with nothing left to watch, switches the health watcher off and stops
   waiting.

With the health watcher installed, check it from the legwork checkout
while smoke is watched (between steps 2 and 5), and again after step 5:

```
orchestration/bin/switchboard-health-ctl status
```

On Windows, run it as `python orchestration/bin/switchboard-health-ctl status`.
It should say on, with a pass logged and no errors, while smoke is
watched, and off after the `DONE`.

## It passed if

- The switchboard showed the decision as written, and tried a push
  notification.
- The answer went through `switchboard-answer`: pane 1 got
  `SWITCHBOARD ANSWER <id>: A`, and `ANSWERED <id>: A` appeared under the
  decision.
- After the cycle the pane title was `orch-smoke` again, and the new
  session wrote "cycle worked" and then `DONE`.
- On the `DONE` the switchboard took smoke off its state file
  (`switchboard-state show` no longer lists it) and stopped.
- With the health watcher: `status` said on while smoke was watched, with
  no errors in the last pass, and off after the `DONE`.

## Clean up

Do this before you close the panes, pass or fail. Left on the switchboard's
watch list, smoke keeps the health watcher (the `switchboard-health` piece)
restarting a switchboard for the throwaway repo. From the legwork checkout:

```
orchestration/bin/switchboard-state remove smoke
orchestration/bin/switchboard-health-ctl off
```

On Windows, put `python` in front of each line. `remove` exits 1, writing
nothing, when smoke is already off the list, and `off` says so when the
watcher is already off. Then close both panes and delete the throwaway
repo.

## Known gotcha

`herdr agent prompt --wait` fails with `agent_prompt_stalled` on slash
commands, because `/clear` and `/rename` never show a working state even
though they land. The switchboard skill skips `--wait` for those and reads
the pane instead.
