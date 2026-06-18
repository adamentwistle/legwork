#!/usr/bin/env python3
"""Local reviewer: the zero-dependency equivalent of the n8n review pipeline.

The novel idea legwork is built around is reviewer-by-exception: an LLM reads
every autonomous session and only escalates to a human when a human decision
is genuinely required. In the optional n8n pipeline that reviewer is an LLM
call in a workflow; this module is the local equivalent, so the headline loop
runs after `git clone` with no n8n, no Telegram and no GitHub PAT.

It is deliberately side-effect-light. Everything here is pure text in, text
out, except call_claude(), which shells out to the `claude` CLI (already a
hard dependency of the runner, so this adds no new dependency). The runner
owns the git writes; this module decides what the verdict means and how the
project file should change, so the decision logic is unit-testable against
fixtures without touching a repo or the network.

Stdlib only, like everything in scripts/. The rubric below is kept in sync
with reviewer/n8n-build-node.js (the n8n copy) and reviewer/rubric.md (the
readable reference); the three say the same thing so the local and n8n
reviewers triage identically.
"""

import json
import re
import subprocess

from legwork_common import parse_frontmatter

# The reviewer system prompt. Verbatim copy of the RUBRIC in
# reviewer/n8n-build-node.js so the local and n8n reviewers judge identically;
# edit both (and the readable mirror in reviewer/rubric.md) together.
RUBRIC = """You are the reviewer in a personal agent pipeline. A Claude Code work session has just ended. You receive evidence: repo, branch, last commit, diff stat, uncommitted file names, test output if any, and the project's tracker entry when one exists. The tracker entry contains the prompt this session was meant to execute, including a Done when line. Your job is triage, not full code review. Reply with JSON only, no prose, no markdown fences.

Verdicts:
- pass: work looks complete and safe, evidence supports the claim.
- revise: something concrete is wrong or unfinished and a fresh session could fix it without the human. You must write the fix prompt.
- escalate: a human decision is required, or policy forces it.

Judging against intent:
- When a tracker entry is present, judge the evidence against its Task and Done when lines. If the evidence satisfies Done when, scope is resolved: never escalate to ask whether the task was the whole task.
- When no tracker entry exists, say so in the summary and judge the work on its own terms. Prefer pass or revise over escalate for ordinary ambiguity in untracked repos.
- Uncommitted files are not by themselves escalation-worthy. Escalate for them only if a filename suggests secrets (.env, key, pem, token, credentials) or the diff touches auth.

Infrastructure exits are not broken environments. When end_reason is runner-recovery, the runner closed the session, not a /wrap, and a test_output that begins with RUNNER: is the runner's note rather than a test result. It means the session may have been cut short by infrastructure: an API overload or 5xx, a usage limit, or the harness exiting before the wrap hook fired. Do not read that note as a broken environment, a broken repo, or a failed setup, and do not treat an empty diff or absent commits as proof the work was bad: nothing was attempted, or the work never reached a commit. Judge the real commits, diff and tests on their merits. If this session's commits and a real diff show the task done, pass; if nothing was committed, prefer revise with a fix prompt that restates the original task and its Done when so a later session retries it. Escalate only if the original task itself forces it under the policy below, never because the runner reported the exit.

Always escalate, regardless of confidence, when the work touches money, payments, billing or transfers; anything deployed, published, sent or public-facing; credentials, secrets or auth; destructive or hard-to-reverse operations; the evidence contradicts the tracker entry's task; the project has already failed review repeatedly, meaning the tracker entry's Log shows two or more prior reviewer 'revise' cycles, since a task that keeps failing review needs a human, not another automatic retry; the session modifies the legwork pipeline itself, meaning the repo under review is legwork and the diff touches its hooks, reviewer rubric, n8n workflow or dashboard build script (scripts/ or reviewer/), since self modification of the pipeline always escalates; or the diff is empty while the commit claims real work. Never pass on prose alone.

session_commits lists the commits made during this session; attribute only those to the session. last_commit may predate the session and must not be judged as this session's work. Weigh evidence over narrative. When torn between pass and revise, choose revise. When torn between revise and escalate, choose escalate.

Output schema:
{"verdict":"pass|revise|escalate","confidence":0.0,"summary":"one line","reasons":["short"],"fix_prompt":"only for revise: complete cold-start prompt (context pointer, one task, Done when line, final step runs /wrap)","decision_brief":{"attempted":"one line","uncertain":"one line","options":["A. ...","B. ..."],"recommendation":"one line that names exactly one option letter"}}
Include fix_prompt only for revise. Include decision_brief only for escalate. The brief must be answerable with one letter, and the recommendation must name exactly one letter."""

VERDICTS = ("pass", "revise", "escalate")
# A verdict only acts on an in-flight project. A session that deliberately
# set done or icebox is respected; the reviewer never resurrects it.
LIVE_STATUSES = ("queued", "running", "review")
SUMMARY_MAX = 180

# The Next prompt is the first fenced block under "## Next prompt". Matches
# legwork_common.PROMPT_RE's shape so a replacement leaves the same structure
# the dashboard and runner read back.
_NEXT_PROMPT_RE = re.compile(
    r"##[ \t]*Next prompt[ \t]*\n+```[a-zA-Z]*\n.*?```", re.S)
_LOG_HEAD_RE = re.compile(r"(##[ \t]*Log[^\n]*\n+)")
_STATUS_LIVE_RE = re.compile(
    r"^status:[ \t]*(?:queued|running|review)[ \t]*$", re.M)
_UPDATED_RE = re.compile(r"^updated:.*$", re.M)


def build_evidence_text(payload):
    """Format the session evidence the same way reviewer/n8n-build-node.js
    does, so the local reviewer reads exactly what the n8n reviewer reads."""
    p = payload
    return "\n".join([
        f"repo: {p.get('repo') or 'unknown'}",
        f"branch: {p.get('branch') or 'unknown'}",
        f"last_commit: {p.get('last_commit') or 'none'}",
        f"uncommitted_files: {p.get('uncommitted_files') or '0'}",
        f"end_reason: {p.get('end_reason') or 'normal'}",
        "",
        "uncommitted file names:",
        p.get("uncommitted_list") or "(none)",
        "",
        "commits made this session:",
        p.get("session_commits") or "(none recorded)",
        "",
        "diff_stat:",
        p.get("diff_stat") or "(empty)",
        "",
        "test_output:",
        p.get("test_output") or "(none provided)",
        "",
        "tracker entry (the prompt this session was meant to execute):",
        p.get("tracker_entry") or "(no tracker entry: untracked repo)",
    ])


def call_claude(prompt, model, claude_path, timeout=300):
    """Run the reviewer as a one-shot `claude -p` call and return the model's
    final text, or None on any failure (non-zero exit, timeout, error
    envelope). The rubric is in the prompt and asks for JSON only; --output-
    format json wraps the reply in a result envelope we unwrap here."""
    argv = [claude_path, "-p", prompt, "--output-format", "json"]
    if model:
        argv += ["--model", model]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        env = json.loads(proc.stdout)
    except ValueError:
        # Not the expected envelope; treat the raw stdout as the reply.
        return proc.stdout or None
    if isinstance(env, dict):
        if env.get("is_error"):
            return None
        return env.get("result", "")
    return proc.stdout or None


def _json_candidates(text):
    """Yield progressively looser slices of `text` that might be the verdict
    JSON: the whole string, a fenced block's body, then first-brace to
    last-brace. Lets parse_verdict survive a model that adds a fence or a
    sentence despite the JSON-only instruction."""
    yield text
    fenced = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    if fenced:
        yield fenced.group(1).strip()
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        yield text[i:j + 1]


def parse_verdict(text):
    """Parse the reviewer's reply into a verdict dict, or None when no JSON
    object carrying a recognised verdict can be recovered."""
    if not text:
        return None
    for candidate in _json_candidates(text.strip()):
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict) and \
                str(obj.get("verdict", "")).lower() in VERDICTS:
            return obj
    return None


def review(payload, model, claude_path):
    """Build the request, call the reviewer, parse the verdict. Returns the
    verdict dict, or None when the call failed or no verdict could be read."""
    prompt = RUBRIC + "\n\nSession evidence:\n\n" + build_evidence_text(payload)
    raw = call_claude(prompt, model, claude_path)
    if raw is None:
        return None
    return parse_verdict(raw)


def _clamp(value, limit=SUMMARY_MAX):
    """Collapse whitespace (newlines included) and cap length, so a verdict
    summary lands on one tidy Log line."""
    collapsed = " ".join((value or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit]


def _strip_fences(body):
    """Remove every triple-backtick fence from a body so it can be safely
    re-wrapped in a ```text block without a nested fence truncating it. Strips
    globally, not just the outer pair: a fix_prompt or brief that contains its
    own fenced code block would otherwise close the wrapper early, and the
    shared (non-greedy) PROMPT_RE would then read back only the head of the
    prompt. Mirrors the n8n Revise node's global strip."""
    body = (body or "").strip()
    body = re.sub(r"```[a-zA-Z]*\n?", "", body)
    body = re.sub(r"```", "", body)
    return body.strip()


def _fenced_next_prompt(body):
    return "## Next prompt\n\n```text\n" + body.strip() + "\n```"


def _replace_next_prompt(text, body):
    """Replace the first fenced block under ## Next prompt with `body`. A
    function replacement (not a backreference string) so a body containing
    backslashes is installed verbatim. Appends a section if none exists."""
    block = _fenced_next_prompt(body)
    new, count = _NEXT_PROMPT_RE.subn(lambda m: block, text, count=1)
    if count:
        return new
    return text.rstrip() + "\n\n" + block + "\n"


def _insert_log_line(text, line):
    """Prepend a dated bullet under ## Log (append-only history), creating the
    section if the file somehow lacks one. Mirrors the runner's repair path."""
    new = _LOG_HEAD_RE.sub(lambda m: m.group(1) + line + "\n", text, count=1)
    if new == text:
        new = text.rstrip() + "\n\n## Log\n\n" + line + "\n"
    return new


def render_decision_brief(brief):
    """Render the reviewer's decision_brief as the DECISION NEEDED block the
    legwork-tracker skill documents (Attempted, Uncertain, lettered Options,
    Recommendation). Starts with DECISION NEEDED, which assess() treats as a
    not-a-prompt marker, so the runner never fires an escalated project even
    if its status were somehow flipped back to queued."""
    brief = brief or {}
    options = [str(o).strip() for o in (brief.get("options") or []) if str(o).strip()]
    lines = ["DECISION NEEDED", ""]
    attempted = str(brief.get("attempted", "")).strip()
    uncertain = str(brief.get("uncertain", "")).strip()
    if attempted:
        lines.append(f"Attempted: {attempted}")
    if uncertain:
        lines.append(f"Uncertain: {uncertain}")
    lines += ["", "Options:"]
    lines += options if options else ["A. (no options provided by the reviewer)"]
    recommendation = str(brief.get("recommendation", "")).strip()
    if recommendation:
        lines += ["", f"Recommendation: {recommendation}"]
    return "\n".join(lines)


def is_actionable(verdict):
    """Whether the local reviewer can act on a verdict without stranding the
    project. A revise needs a usable fix prompt (n8n guards on this too); pass
    and escalate always carry enough to act on. Unactionable verdicts are
    parked for a human rather than written back."""
    if (verdict.get("verdict") or "").lower() == "revise":
        return bool(_strip_fences(verdict.get("fix_prompt", "")))
    return True


def apply_verdict(text, verdict, now):
    """Apply a reviewer verdict to a project file's text. Pure: returns
    (new_text, new_status, detail) or None when the verdict is unknown or the
    project sits in a terminal state (done/icebox) that must not be
    resurrected.

    Mirrors the n8n review write-back: pass and revise requeue, escalate
    flips to escalated, and each prepends a dated Log line. revise installs
    the fix_prompt as the Next prompt. The one local addition over n8n is that
    escalate also writes the DECISION NEEDED brief into the Next prompt block:
    n8n carries the brief to Telegram and lets reply-capture mint the next
    prompt, but local review has neither, so writing the brief into the file
    is what surfaces the decision on the dashboard's Needs-you zone."""
    name = (verdict.get("verdict") or "").lower()
    if name not in VERDICTS:
        return None
    meta = parse_frontmatter(text)
    if meta.get("status", "").lower() not in LIVE_STATUSES:
        return None
    today = now.strftime("%Y-%m-%d")
    summary = _clamp(verdict.get("summary", ""))

    if name == "pass":
        new_status = "queued"
        detail = f"Reviewer passed: {summary} Requeued with the wrapped prompt."
    elif name == "revise":
        fix = _strip_fences(verdict.get("fix_prompt", ""))
        if not fix:
            # A revise with no usable fix prompt would otherwise overwrite the
            # wrapped prompt with an empty block and strand the project. n8n
            # writes nothing here and leans on Telegram + reply-capture; with
            # neither locally, refuse the verdict so the caller parks it for a
            # human (see is_actionable / run_local_review).
            return None
        new_status = "queued"
        text = _replace_next_prompt(text, fix)
        detail = f"Reviewer revise: {summary} Fix prompt installed and requeued."
    else:  # escalate
        new_status = "escalated"
        # Strip any fences out of the rendered brief too, so a brief field
        # carrying a code snippet cannot truncate the Next prompt block.
        text = _replace_next_prompt(
            text, _strip_fences(render_decision_brief(verdict.get("decision_brief"))))
        detail = f"Reviewer escalated: {summary}"

    text = _STATUS_LIVE_RE.sub(f"status: {new_status}", text, count=1)
    text = _UPDATED_RE.sub(f"updated: {today}", text, count=1)
    text = _insert_log_line(text, f"- {today}: {detail}")
    return text, new_status, detail


def verdict_alert(verdict, stem):
    """The Telegram-style message for a verdict, matching the n8n letters
    (PASS / REVISE / NEEDS YOU). Sent only when an alert webhook is wired;
    with none, the dashboard is the surface and this is unused."""
    name = (verdict.get("verdict") or "").lower()
    summary = (verdict.get("summary") or "").strip()
    if name == "pass":
        conf = verdict.get("confidence")
        pct = f" ({int(round(float(conf) * 100))}%)" \
            if isinstance(conf, (int, float)) else ""
        return f"PASS  {stem}\n{summary}{pct}"
    if name == "revise":
        reasons = "; ".join(str(r) for r in (verdict.get("reasons") or []) if r)
        why = f"\n\nWhy: {reasons}" if reasons else ""
        fix = _strip_fences(verdict.get("fix_prompt", ""))
        fixpart = f"\n\nFix prompt (queued for next session):\n{fix}" if fix else ""
        return f"REVISE  {stem}\n{summary}{why}{fixpart}"
    if name == "escalate":
        brief = verdict.get("decision_brief") or {}
        attempted = (str(brief.get("attempted", "")).strip() or summary)
        uncertain = str(brief.get("uncertain", "")).strip()
        options = "\n".join(str(o).strip() for o in (brief.get("options") or []) if str(o).strip())
        recommendation = str(brief.get("recommendation", "")).strip()
        return (f"NEEDS YOU  {stem}\n\nAttempted: {attempted}\n"
                f"Uncertain: {uncertain}\n\n{options}\n\n"
                f"Recommendation: {recommendation}\n\nReply with a letter.")
    return f"{name.upper()}  {stem}\n{summary}"
