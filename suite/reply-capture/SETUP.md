# Legwork reply capture

Replying to any review letter in Telegram drives the project from your
phone, and slash commands to the same bot drive the whole queue. The
workflow reads which letter you replied to (the first line is always
`NEEDS YOU <project>`, `PASS <project>` or `REVISE <project>`) and
acts on the matching `projects/<name>.md` on the GitHub remote:

- `NEEDS YOU` + a single option letter: the decision is logged, the status
  flips to queued, and a fresh prompt is minted that carries out the chosen
  option. Replying `continue` instead takes the brief's recommendation.
- `PASS` + `continue`: the status flips to queued and the prompt minted at
  /wrap stands. For autonomy: loop projects the runner then fires it, so one
  word keeps the project moving with no manual prompt copy.
- `REVISE` + `continue`: the reviewer's fix prompt is lifted out of the
  letter, installed as the Next prompt, and the project is queued.

Continue words: go, continue, cont, carry on, keep going, y, yes, ok, okay,
next, proceed. Decision letters must be exactly one letter (a trailing
`.`/`)`/`!` is fine) and must be one of the options the brief offered;
anything else gets a nudge message and writes nothing.

## Commands

Plain messages to the bot (no reply needed) starting with `/` hit the
command branch. Everything reads and writes your legwork repo on
GitHub through the same Contents API credential; nothing touches the machine
directly, the runner picks changes up on its next five minute tick.

- `/board` (or `/status`): every project grouped by what it needs from you:
  NEEDS YOU, in review, running, queued and firing on its own, queued
  waiting on you, blocked, plus icebox and done counts.
- `/show <project>`: status, autonomy, last three log lines and the current
  Next prompt (truncated to fit Telegram).
- `/fire <project>`: queue one session. A project with autonomy: loop and a
  Vision just flips to queued; anything else also gets `fire_once: <date>`,
  the one-shot consent the runner consumes on claim. Refuses done, icebox,
  running, blocked_on projects and marker prompts.
- `/loop <project>`: grant standing autonomy (`autonomy: loop`). Refused
  when the file has no ## Vision section: run /vision at the desk first,
  autonomy needs its standing brief.
- `/prompt <project> <text>`: your text goes through the same minter model
  as decision replies, the shaped prompt is installed as the Next prompt
  and the project is queued. Non-loop projects then need `/fire` to run.
- `/pause` and `/resume`: commit or delete the tracked
  `.runner-pause-remote` flag. The runner honors it even when it arrives
  with the tick's own pull, so /pause stops the very next tick.
- `/help`: the command list.

Unknown commands and missing arguments get a help reply and write nothing.
Non-command messages that are not review-letter replies are ignored.

## Where it lives

- Workflow: "Legwork reply capture", imported into your own n8n instance.
- Importable source: `reply-capture/n8n-reply-capture-workflow.json` (this
  directory). Credential ids and the GitHub owner/repo are placeholders in
  the committed copy (REPLACE_WITH_...); fill them in after import.
- Generator: the JSON was produced from a builder script; edit the JSON
  directly for small changes, or regenerate if you keep the builder.

## Flow

```
Reply trigger (Telegram, LegworkTG)
  -> Is review reply?      (replied-to text starts NEEDS YOU / PASS / REVISE;
                            false branch -> Parse command, the slash command
                            router: /board lists projects/ and fetches each
                            file raw, /show /fire /loop /prompt fetch one
                            file and apply the edit (only /prompt calls the
                            minter), /pause and /resume create or delete
                            .runner-pause-remote; every path ends in a
                            Telegram reply, failures included)
  -> Extract               (kind + repo from the letter's first line; the reply
                            classified as a decision letter or a continue word;
                            everything validated, including that a decision
                            letter is one the brief actually offered)
  -> Valid decision?       (otherwise a nudge message, nothing written)
  -> GitHub get file       (Contents API: read projects/<repo>.md + sha)
  -> Build write-back      (prepend the right log line, flip escalated/review/
                            running -> queued, bump updated; decisions also get
                            a minter request, with the Vision section included
                            when the file has one)
  -> Needs mint?           (only decisions mint; continue paths skip the model)
  -> Call minter           (Anthropic: system prompt is the prompt shape rules)
  -> Finalize content      (decision: install the minted prompt or a marker;
                            PASS continue: keep the existing prompt; REVISE
                            continue: install the fix prompt from the letter)
  -> GitHub put file       (Contents API: commit the updated file)
  -> Telegram confirm      (one line: what was queued, and whether the runner
                            will fire it)
```

Failure paths each send you a Telegram note and write nothing: an unreadable
reply, a missing project file, or a failed commit all tell you so. If only
the prompt mint fails, the decision and the queued flip are still committed
and the Next prompt is replaced with a marker, never left as the stale brief.
done and icebox projects are never resurrected by a reply: only escalated,
review and running flip to queued.

## One time setup

The committed workflow references three n8n credentials through
`REPLACE_WITH_` placeholder ids: the Telegram bot (`LegworkTG`), the GitHub
write-back token (`GitHub legwork contents`), and the Anthropic API key
(`Anthropic`). None of them ship in the repo and none exist on your n8n
instance until you create them, so every node shows a missing credential
after import. Create all three, attach them, and restrict the trigger to
your own Telegram user id before activating.

### 1. Create the Telegram bot and its credential

- In Telegram, message @BotFather, send `/newbot`, and follow the prompts
  to get a bot token.
- In n8n, create a credential of type "Telegram API" named `LegworkTG` and
  paste the token in.
- If you already created `LegworkTG` for the review or alert pipelines,
  reuse it: one bot serves all three. (But see the caveat under Activate:
  only one workflow may hold a Telegram Trigger on a given bot.)

### 2. Create the GitHub PAT (human only)

The write-back token never lives in this repo. Create a GitHub fine-grained
personal access token:

- Resource owner: your personal GitHub account.
- Repository access: Only select repositories -> your legwork repo.
- Repository permissions: Contents -> Read and write. Nothing else.
- Expiration: your call. Set a reminder to rotate it.

### 3. Store it as an n8n credential

In n8n, create a credential of type "Header Auth":

- Name: `GitHub legwork contents`
- Header Name: `Authorization`
- Header Value: `Bearer <the PAT you just created>`

### 4. Create the Anthropic credential

The two minter nodes call the Anthropic Messages API directly over HTTP, so
the key is stored the same way:

- Name: `Anthropic`
- Type: "Header Auth"
- Header Name: `x-api-key`
- Header Value: your Anthropic API key (from console.anthropic.com). The
  `anthropic-version` header is already set on the nodes themselves.

### 5. Attach the credentials

Open the imported "Legwork reply capture" workflow and set the matching
credential on every node that needs one:

- `LegworkTG` on the "Reply trigger" and each Telegram reply node.
- `GitHub legwork contents` on every GitHub Contents API node: "GitHub get
  file", "GitHub put file", "List projects", "Get each project", "Cmd get
  file", "Cmd put file", "Get pause flag", "Pause create", "Pause delete".
- `Anthropic` on "Call minter" and "Cmd call minter".

### 6. Restrict the trigger to your Telegram user id

The trigger fires on any message anyone sends the bot: nothing in the
workflow checks who the sender is, and the command branch reaches the
GitHub write-back, so without this step anyone who learns the bot's handle
can mint prompts and queue sessions on your repo (SECURITY.md covers why).
Treat it as required, not optional:

- Find your numeric Telegram user id: message @userinfobot, or read
  `message.from.id` off any execution of the trigger in n8n.
- Open the "Reply trigger" node, add the additional field "Restrict to
  User IDs", and enter your id. The trigger then drops messages from
  anyone else before the workflow runs.

### 7. Activate

Toggle the workflow Off then On in the n8n UI. This instance registers the
trigger route only on UI activation: a REST API activate sets active=true in
the DB but the running process does not pick up the Telegram webhook, so
replies are silently ignored until you toggle it once in the UI.

Caveat: a Telegram bot allows only one update consumer. The review pipeline
only sends with LegworkTG, so there is no existing trigger to clash with. If
you ever add another Telegram Trigger on the same bot, route both through one
trigger instead, or use a second bot.

## Test plan

1. In Telegram, with your bot, send yourself the escalation brief so there is
   a `NEEDS YOU` message to reply to. The first line must be
   `NEEDS YOU  example-project`. (Before testing, set example-project to
   `escalated` on the remote so the queued flip is observable.)
2. Reply to that message with a single letter, for example `C`.
3. Confirm the bot replies with a one line confirmation.
4. On GitHub, open `projects/example-project.md` and confirm:
   - a new newest log line `Human decision via Telegram: C`,
   - `status: queued`,
   - a fresh shaped Next prompt (not the old DECISION NEEDED brief).
5. Locally, `git pull --rebase` to sync the remote write back down.
6. For the continue verb: send yourself a fake pass letter whose first line
   is `PASS  <project>` (pick a project sitting in review on the remote),
   reply `continue`, and confirm the status flips to queued, a log line
   records the acknowledgement, and the Next prompt block is untouched.

## Notes

- Dates are computed in Europe/London inside the workflow.
- The owner and repo (the `REPLACE_WITH_OWNER/REPLACE_WITH_REPO` placeholders,
  branch `main`) are baked into the GitHub node URLs. Set them there after
  import, and change them again if the remote ever moves.
- The minter uses claude-sonnet-4-6, the same model and credential as the
  reviewer. The system prompt mirrors the prompt shape rules in
  `core/skills/legwork-tracker/SKILL.md`; keep them aligned if the rules
  change.
