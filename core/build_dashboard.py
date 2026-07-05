#!/usr/bin/env python3
"""Build dashboard/index.html from projects/*.md.

Zero dependencies. Run from anywhere:

    python3 core/build_dashboard.py

Reads simple frontmatter (key: value lines between --- markers), the first
fenced block under '## Next prompt', and bullet lines under '## Log'.
The HTML is a build artifact: every run replaces it wholesale. All styling
lives in CSS below; project data never lives in this file.

The visual design is a neo-brutalist system in the loud, pop-palette
register: a cream paper canvas, hot-red / vivid-yellow / soft-violet
color blocking, 4px ink borders on every element, solid offset shadows
with zero blur, sharp corners, massive 900-weight uppercase display
type, sticker rotations, a stats marquee, and mechanical push/lift
interactions. Status colors are high-saturation highlighter hues,
always ink-bordered and text-labeled so color never carries meaning
alone. The page stays a single offline artifact: no external fonts, no
requests. Only the data binding lives in the Python; all styling is in
CSS below.
"""

import html
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from legwork_common import COST_RE, parse_frontmatter, PROMPT_RE, parse_date

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
OUT_FILE = ROOT / "dashboard" / "index.html"

STATUSES = ["escalated", "queued", "running", "review", "done", "icebox"]
ORDER = {s: i for i, s in enumerate(STATUSES)}
STATUS_LABEL = {
    "escalated": "needs you", "queued": "ready", "running": "running",
    "review": "in review", "done": "done", "icebox": "icebox",
}
# status -> short code used by the .s-* CSS classes and --st-* tokens
SCODE = {
    "escalated": "esc", "queued": "que", "running": "run",
    "review": "rev", "done": "don", "icebox": "ice",
}
STALE_DAYS = 14

LOG_RE = re.compile(r"##\s*Log\s*\n(.*?)(?:\n##|\Z)", re.S)
DATED_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):\s*(.+)$", re.S)
LOG_ROW_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}):\s*(.*)$", re.S)


def parse_project(path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # One unreadable or non-UTF-8 file must not abort the whole build.
        print(f"Warning: skipping {path.name}: {exc}", file=sys.stderr)
        return None
    meta = parse_frontmatter(text)
    if not meta.get("name"):
        return None

    prompt_match = PROMPT_RE.search(text)
    prompt = prompt_match.group(1).strip() if prompt_match else ""

    log_match = LOG_RE.search(text)
    log_lines = []
    if log_match:
        for line in log_match.group(1).splitlines():
            line = line.strip()
            if line.startswith("- "):
                log_lines.append(line[2:])

    status = meta.get("status", "queued").lower()
    if status not in STATUSES:
        print(f"Warning: {path.name} has unknown status '{status}', treating as queued", file=sys.stderr)
        status = "queued"

    # Freshness is the newest of the frontmatter date and any log entry
    # (all of them, since a hand-edited log is not always newest-first), so
    # a forgotten `updated` bump cannot make a project look stale. Clamped
    # at zero: a future-dated entry reads as "today", not negative days.
    candidates = [parse_date(meta.get("updated", ""))]
    candidates.extend(parse_date(line) for line in log_lines)
    dates = [c for c in candidates if c]
    days_quiet = max(0, (date.today() - max(dates)).days) if dates else None

    return {
        "file": path.name,
        "name": meta.get("name", path.stem),
        "category": meta.get("category", "personal").lower(),
        "status": status,
        "energy": meta.get("energy", ""),
        "description": meta.get("description", ""),
        "repo": meta.get("repo", ""),
        "updated": meta.get("updated", ""),
        "autonomy": meta.get("autonomy", "").lower(),
        "blocked_on": meta.get("blocked_on", ""),
        "days_quiet": days_quiet,
        "prompt": prompt,
        "log": log_lines[:3],
        "log_all": log_lines,
    }


def runner_activity(files):
    """Optional, defensive read of runner.log. Returns a (per_file, cost_today)
    pair where per_file maps a project filename to its most recent fire time and
    last cost, and cost_today is the summed cost of all 'completed' lines today.
    A missing or unreadable log yields ({}, 0.0) and never raises — the runner
    is not guaranteed to have run, and the tests build in a sandbox without it."""
    per_file = {}
    cost_today = 0.0
    try:
        log_path = ROOT / "runner.log"
        if not log_path.is_file():
            return per_file, cost_today
        today = date.today().isoformat()
        names = {f for f in files}
        for line in log_path.read_text(encoding="utf-8").splitlines():
            when = line[:16]
            # the runner writes "<19-char stamp>  <message>"
            rest = line[19:].lstrip()
            if rest.startswith("fired "):
                fname = rest[6:].split(" in ", 1)[0].strip()
                if fname in names:
                    per_file.setdefault(fname, {})["fired"] = when
            elif rest.startswith("completed "):
                fname = rest[10:].split(":", 1)[0].strip()
                cost_match = COST_RE.search(rest)
                cost = float(cost_match.group(1)) if cost_match else None
                if fname in names and cost is not None:
                    per_file.setdefault(fname, {})["cost"] = cost
                if cost is not None and line.startswith(today):
                    cost_today += cost
    except Exception:
        return {}, 0.0
    return per_file, cost_today


# ----------------------------------------------------------------------------
# inline icons (kept here so the markup builders stay readable)
# ----------------------------------------------------------------------------
IC_COPY = ('<svg class="ic-copy" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
           'stroke-width="2"><rect x="9" y="9" width="11" height="11" rx="2"/>'
           '<path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>')
IC_CHECK = ('<svg class="ic-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M20 6 9 17l-5-5"/></svg>')
IC_CHEVRON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
              'stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>')
IC_LOCK = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
           'stroke-linecap="round"><rect x="4" y="10" width="16" height="11" rx="2"/>'
           '<path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>')
IC_CLOCK = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
            'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/>'
            '<path d="M12 7v5l3 2"/></svg>')
IC_AUTO = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
           'stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8'
           'M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"/></svg>')
IC_ALERT = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/>'
            '<path d="M12 8v4M12 16h.01"/></svg>')
IC_STAR = ('<svg class="star" viewBox="0 0 24 24" fill="var(--second)" stroke="currentColor" '
           'stroke-width="1.8" stroke-linejoin="round" aria-hidden="true">'
           '<path d="M12 2l2.9 6.3 6.9.6-5.2 4.6 1.5 6.7-6.1-3.6-6.1 3.6 1.5-6.7L2.2 8.9l6.9-.6L12 2z"/></svg>')


def _is_stale(p):
    d = p["days_quiet"]
    return d is not None and d > STALE_DAYS and p["status"] not in ("icebox", "done")


def _freshness_badge(p):
    d = p["days_quiet"]
    if d is None:
        return ""
    label = "today" if d == 0 else f"{d}d quiet"
    cls = "badge fresh-stale" if d > STALE_DAYS else "badge"
    return f'<span class="{cls}">{label}</span>'


def _fired_line(p):
    """A 'last fired <when> $<cost>' line when the runner has touched this
    project. Empty when there is no runner activity for it."""
    act = p.get("activity") or {}
    when = act.get("fired")
    if not when:
        return ""
    cost = act.get("cost")
    cost_part = f' &middot; ${cost:.2f}' if isinstance(cost, (int, float)) else ""
    return (f'<div class="fired-line">{IC_AUTO}<span>last fired '
            f'{html.escape(when)}{cost_part}</span></div>')


def status_pill(p):
    """The status indicator shown top-right of a card. The .running class
    drives the pulsing dot; color comes from the card's .s-* class."""
    return (f'<span class="pill {p["status"]}"><span class="dot"></span>'
            f'{STATUS_LABEL[p["status"]]}</span>')


def pills(p):
    """The badge row below the description: signal badges (blocked / stale /
    auto) first, then the neutral category, energy and freshness badges."""
    out = []
    if p["blocked_on"]:
        out.append(f'<span class="badge blocked">{IC_LOCK}blocked</span>')
    if _is_stale(p):
        out.append(f'<span class="badge stale">{IC_CLOCK}stale</span>')
    if p["autonomy"] == "loop":
        out.append(f'<span class="badge auto">{IC_AUTO}auto</span>')
    out.append(f'<span class="badge cat">{html.escape(p["category"])}</span>')
    if p["energy"]:
        out.append(f'<span class="badge energy">{html.escape(p["energy"])}</span>')
    out.append(_freshness_badge(p))
    return "".join(out)


def log_rows(entries):
    rows = []
    for e in entries:
        m = LOG_ROW_RE.match(e)
        if m:
            when = f"{m.group(2)}-{m.group(3)}"
            text = m.group(4)
        else:
            when = ""
            text = e
        rows.append(f'<div class="log-row"><span class="log-date">{html.escape(when)}</span>'
                    f'<span class="log-text">{html.escape(text)}</span></div>')
    return "".join(rows)


def card(p, hero=False):
    sc = SCODE.get(p["status"], "que")
    cls = f'card hero s-{sc}' if hero else f'card s-{sc}'

    blocked_block = ""
    if p["blocked_on"]:
        blocked_block = (f'<div class="blocked-line">{IC_ALERT}'
                         f'<span><b>Blocked on:</b> {html.escape(p["blocked_on"])}</span></div>')

    recent_block = ""
    recent = log_rows(p["log"])
    if recent:
        recent_block = f'<div class="log-cap">Recent activity</div><div class="log">{recent}</div>'

    exp_block = ""
    foot_block = ""
    if p["prompt"]:
        full = log_rows(p["log_all"])
        full_block = f'<div class="log-cap">Full log</div><div class="log">{full}</div>' if full else ""
        exp_block = (
            '<div class="expander"><div class="exp-body">'
            '<div class="prompt-cap"><span class="log-cap" style="margin:0;">Next prompt</span>'
            f'<button class="btn-copy-ghost copy-src" data-from="card">{IC_COPY}{IC_CHECK}'
            '<span class="copy-label">copy</span></button></div>'
            f'<pre class="prompt">{html.escape(p["prompt"])}</pre>'
            f'{full_block}</div></div>'
        )
        foot_block = (
            '<div class="card-foot">'
            f'<button class="exp-toggle">{IC_CHEVRON}<span class="exp-label">Prompt &amp; log</span></button>'
            '<div class="foot-spacer"></div>'
            f'<button class="btn-copy copy-src" data-from="card">{IC_COPY}{IC_CHECK}'
            '<span class="copy-label">Copy prompt</span></button>'
            '</div>'
        )

    data_prompt = html.escape(p["prompt"], quote=True)
    return (
        f'<article class="{cls}" data-status="{p["status"]}" '
        f'data-category="{html.escape(p["category"])}" data-prompt="{data_prompt}">'
        '<div class="card-top"><div class="card-id">'
        f'<h3 class="card-name">{html.escape(p["name"])}</h3>'
        f'<p class="card-desc">{html.escape(p["description"])}</p></div>'
        f'{status_pill(p)}</div>'
        f'<div class="badges">{pills(p)}</div>'
        f'{_fired_line(p)}{blocked_block}{recent_block}{exp_block}{foot_block}'
        '</article>'
    )


def changelog_html(projects):
    """Every dated log line across every project, newest day first, as a
    timeline. Each entry is tagged with its project and carries that
    project's current status color. This is the one place to see what moved."""
    by_day = {}
    for p in projects:
        sc = SCODE.get(p["status"], "que")
        for line in p["log_all"]:
            m = DATED_LINE_RE.match(line)
            if not m:
                continue
            by_day.setdefault(m.group(1), []).append(
                (ORDER[p["status"]], p["name"], sc, m.group(2)))
    if not by_day:
        return '<p class="sec-sub">No log entries yet.</p>'

    today = date.today()
    sections = []
    for day in sorted(by_day, reverse=True):
        dt = parse_date(day)
        if dt:
            delta = (today - dt).days
            if delta == 0:
                label = "Today"
            elif delta == 1:
                label = "Yesterday"
            else:
                label = f"{dt.strftime('%b')} {dt.day}"
            sub = f"{day} &middot; {dt.strftime('%A')}"
        else:
            label = day
            sub = day
        entries = "".join(
            f'<div class="entry s-{sc}"><span class="entry-dot"></span>'
            f'<span class="entry-proj">{html.escape(name)}</span>'
            f'<span class="entry-text">{html.escape(text)}</span></div>'
            for _, name, sc, text in sorted(by_day[day])
        )
        sections.append(
            f'<div class="day"><span class="day-node"></span>'
            f'<div class="day-head"><span class="day-label">{html.escape(label)}</span>'
            f'<span class="day-date">{sub}</span></div>{entries}</div>'
        )
    return "".join(sections)


def sort_key(p):
    quiet = p["days_quiet"] if p["days_quiet"] is not None else 0
    # Stalest first for queued (the ones most at risk of dying), freshest
    # first for everything else.
    tiebreak = -quiet if p["status"] == "queued" else quiet
    return (ORDER[p["status"]], tiebreak, p["name"].lower())


ALLCLEAR = (
    '<div class="allclear"><span class="allclear-ic">'
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>'
    '</span><div><h3>Nothing needs you</h3>'
    '<p>The queue is running clean &mdash; no escalations waiting on a human.</p></div></div>'
)


def build(projects):
    # Optional runner activity — folded onto cards and the masthead. Stays
    # ({}, 0.0) when runner.log is absent, so this is a no-op in the sandbox.
    activity, cost_today = runner_activity({p["file"] for p in projects})
    for p in projects:
        p["activity"] = activity.get(p["file"])

    escalated = [p for p in projects if p["status"] == "escalated"]
    rest = sorted([p for p in projects if p["status"] != "escalated"], key=sort_key)

    total = len(projects)
    active = sum(1 for p in projects if p["status"] in ("escalated", "queued", "running", "review"))
    counts = {s: sum(1 for p in projects if p["status"] == s) for s in STATUSES}
    categories = sorted({p["category"] for p in projects})

    today_metric = date.today().strftime("%a %d %b").upper()
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Shown only when the runner has spent something today; absent otherwise.
    cost_metric = (
        f'<div class="metric"><span class="m-l">Cost today</span>'
        f'<span class="m-v">${cost_today:.2f}</span></div>'
        if cost_today > 0 else ""
    )

    # queue ribbon
    ribbon_bar = "".join(
        f'<span style="--d:var(--st-{SCODE[s]});flex:{counts[s]}"></span>'
        for s in STATUSES if counts[s] > 0
    )
    ribbon_legend = "".join(
        f'<span class="leg{"" if counts[s] else " leg-z"}">'
        f'<span class="leg-dot" style="--d:var(--st-{SCODE[s]})"></span>'
        f'<span class="leg-name">{s.capitalize()}</span>'
        f'<span class="leg-n">{counts[s]}</span></span>'
        for s in STATUSES
    )

    # needs-you zone
    if escalated:
        needs_body = ('<div class="needs-zone" id="needsZone"><div class="needs-grid">'
                      + "".join(card(p, hero=True) for p in escalated)
                      + '</div></div>')
    else:
        needs_body = f'<div id="needsZone">{ALLCLEAR}</div>'

    # category chips
    cat_chips = (f'<button class="chip" data-cat="all" aria-pressed="true">'
                 f'All <span class="cc">{total}</span></button>')
    for c in categories:
        n = sum(1 for p in projects if p["category"] == c)
        cat_chips += (f'<button class="chip" data-cat="{html.escape(c)}" aria-pressed="false">'
                      f'{html.escape(c.capitalize())} <span class="cc">{n}</span></button>')

    grid = "".join(card(p) for p in rest)

    # marquee — live stats as a repeating strip; two identical halves scroll -50%
    mq_bits = ["legwork", f"{total} projects", f"{active} active",
               f"{len(escalated)} need you"]
    if cost_today > 0:
        mq_bits.append(f"${cost_today:.2f} today")
    mq_seg = "".join(f'{html.escape(b)}<span class="mq-s">&#9733;</span>' for b in mq_bits)
    mq_half = mq_seg * 3

    body = f"""
<div class="marquee" aria-hidden="true">
  <div class="marquee-track"><span class="mq-half">{mq_half}</span><span class="mq-half">{mq_half}</span></div>
</div>
<header class="masthead">
  <div class="wrap">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="1.5" y="1.5" width="13" height="3" rx="1" fill="currentColor"/><rect x="1.5" y="6.5" width="13" height="3" rx="1" fill="currentColor" opacity="0.7"/><rect x="1.5" y="11.5" width="9" height="3" rx="1" fill="currentColor" opacity="0.45"/></svg>
      </span>
      <span class="brand-name">legwork</span>
      <span class="brand-sub">queue</span>
    </div>
    <div class="mast-spacer"></div>
    <div class="metrics" aria-label="Queue summary">
      <div class="metric"><span class="m-l">Date</span><span class="m-v">{today_metric}</span></div>
      <div class="metric"><span class="m-l">Projects</span><span class="m-v">{total}</span></div>
      <div class="metric"><span class="m-l">Active</span><span class="m-v">{active}</span></div>
      {cost_metric}
      <div class="metric needs"><span class="m-l">Needs you</span><span class="m-v">{len(escalated)}</span></div>
    </div>
    <div class="head-actions">
      <a class="changelog-link icon-btn" href="#changelog" title="Jump to changelog" aria-label="Jump to changelog">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.4 2.6L3 8"/><path d="M3 4v4h4"/><path d="M12 8v4l3 2"/></svg>
      </a>
      <button class="theme-toggle icon-btn" id="themeToggle" aria-label="Toggle color theme" title="Toggle theme">
        <svg class="ic-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h2M20 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/></svg>
        <svg class="ic-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"/></svg>
      </button>
    </div>
  </div>
</header>

<main>

  <div class="wrap">
    <section class="ribbon" aria-label="Queue distribution">
      <div class="ribbon-bar" role="img" aria-label="Project count by status">{ribbon_bar}</div>
      <div class="ribbon-legend">{ribbon_legend}</div>
    </section>
  </div>

  <section class="sec" id="needs">
    <div class="wrap">
      <div class="sec-head">
        <div class="sec-head-row">
          <span class="sec-label" style="--lbl:var(--st-esc)">Reviewer escalations</span>
          <span class="sec-count mono" id="needsCount">{len(escalated)}</span>
          {IC_STAR}
        </div>
        <h2 class="sec-title">Needs <span class="hollow">you</span></h2>
        <p class="sec-sub">Escalated by the reviewer &mdash; these are blocked on a human.</p>
      </div>
      {needs_body}
    </div>
  </section>

  <section class="sec">
    <div class="wrap">
      <div class="sec-head">
        <div class="sec-head-row">
          <span class="sec-label">The queue</span>
        </div>
        <h2 class="sec-title">Projects</h2>
      </div>

      <div class="filters">
        <div class="chips" id="catFilter" role="group" aria-label="Filter by category">
          {cat_chips}
        </div>
        <div class="segmented" id="stateFilter" role="group" aria-label="Filter by state">
          <button class="seg" data-state="everything" aria-pressed="true">Everything</button>
          <button class="seg" data-state="actionable" aria-pressed="false">Actionable</button>
          <button class="seg" data-state="done" aria-pressed="false">Done</button>
          <button class="seg" data-state="icebox" aria-pressed="false">Icebox</button>
        </div>
      </div>

      <div class="grid" id="grid">
        {grid}
        <div class="empty" id="emptyState" hidden>No projects match this filter.</div>
      </div>
    </div>
  </section>

  <section class="sec band band-violet" id="changelog">
    <div class="wrap">
      <div class="sec-head">
        <div class="sec-head-row">
          <span class="sec-label">Every dated entry, newest first</span>
          {IC_STAR}
        </div>
        <h2 class="sec-title boxed">Changelog</h2>
      </div>
      <div class="timeline">
        {changelog_html(projects)}
      </div>
    </div>
  </section>

</main>

<footer class="foot">
  <div class="wrap">
    <span class="foot-brand">legwork</span>
    <span class="foot-tag">autonomous project queue for Claude Code</span>
    <span class="foot-spacer"></span>
    <span class="mono foot-gen">generated {generated}</span>
  </div>
</footer>
"""

    return HEAD_OPEN + CSS + HEAD_CLOSE + body + SCRIPT


HEAD_OPEN = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta http-equiv="refresh" content="120" />
<title>Legwork &mdash; project queue</title>
<style>
"""

CSS = """
/* ============================================================
   LEGWORK — neo-brutalist design system, pop register
   The screen is a collage board, not glass: 4px ink borders on
   every element, solid offset shadows with zero blur (4/8/12/
   16px ladder), sharp corners, massive 900-weight uppercase
   display type, sticker rotations, and hot-red / vivid-yellow /
   soft-violet color blocking on a cream paper canvas. Buttons
   press down mechanically; cards lift. Every colored mark is
   ink-bordered and text-labeled so color never carries meaning
   alone. No blur, no gradients, no mid-grays. 'Space Grotesk'
   renders when installed; the stack falls back to a heavy
   system sans (fonts deliberately stay offline: the page makes
   no external requests).
   ============================================================ */
:root{
  --font-sans:'Space Grotesk', ui-sans-serif, system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif;
  --font-mono:ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  --fs-xs:11px; --fs-sm:12px; --fs-base:14px; --fs-md:16px; --fs-lg:19px;
  --fs-xl:24px; --fs-2xl:32px;
  --fs-display:clamp(46px, 7vw, 78px);
  --fs-boxed:clamp(34px, 5vw, 58px);
  --lh-tight:1.06; --lh-snug:1.35; --lh-normal:1.55;

  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:20px; --s6:24px;
  --s8:32px; --s10:40px; --s12:48px; --s16:64px;

  /* paper / ink / pop palette */
  --paper:#FFFDF5;
  --card:#FFFFFF;
  --ink:#000000;
  --ink-soft:rgba(0,0,0,0.6);   /* long-form secondary text only */
  --grid-line:rgba(0,0,0,0.08);
  --accent:#FF6B6B; --second:#FFD93D; --violet:#C4B5FD;

  --bw:4px;
  --sh-sm:4px 4px 0 0 var(--ink);
  --sh-md:8px 8px 0 0 var(--ink);
  --sh-lg:12px 12px 0 0 var(--ink);
  --sh-xl:16px 16px 0 0 var(--ink);

  /* status — identical highlighter hues in both themes; every use is
     ink-bordered and text-labeled (validated: chroma floor + CVD pass) */
  --st-esc:#FF6B6B; --st-que:#4D96FF; --st-run:#FFD93D;
  --st-rev:#C4B5FD; --st-don:#6BCB77; --st-ice:#66D9E8;
  --st-stale:#FFA94D;
}

/* dark: opt-in via the toggle only — the definitive palette is light.
   The collage board flips to blackboard: canvas near-black, panels pure
   black, and the ink (borders, text, shadows) becomes cream. The
   highlighter hues stay put; black label text stays legible on all of
   them. The violet changelog band stays a printed light island. */
:root[data-theme="dark"]{ color-scheme:dark;
  --paper:#121212;
  --card:#000000;
  --ink:#FFFDF5;
  --ink-soft:rgba(255,253,245,0.62);
  --grid-line:rgba(255,253,245,0.07);
}

.s-esc{--s:var(--st-esc);}
.s-que{--s:var(--st-que);}
.s-run{--s:var(--st-run);}
.s-rev{--s:var(--st-rev);}
.s-don{--s:var(--st-don);}
.s-ice{--s:var(--st-ice);}

/* base */
*{box-sizing:border-box;}
*::selection{background:var(--second);color:#000;}
html{-webkit-text-size-adjust:100%;}
body{
  margin:0; font-family:var(--font-sans); font-size:var(--fs-base);
  font-weight:500; line-height:var(--lh-normal); color:var(--ink);
  background-color:var(--paper);
  background-image:
    linear-gradient(to right, var(--grid-line) 1px, transparent 1px),
    linear-gradient(to bottom, var(--grid-line) 1px, transparent 1px);
  background-size:40px 40px;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
h1,h2,h3,p{margin:0;}
a{color:inherit;text-decoration:none;}
button{font-family:inherit;cursor:pointer;border:none;background:none;color:inherit;}
.mono{font-family:var(--font-mono);font-variant-numeric:tabular-nums;}
.wrap{max-width:1240px;margin:0 auto;padding:0 var(--s6);}
:focus-visible{outline:3px solid var(--ink);outline-offset:2px;}

/* marquee — an inverted strip of live queue stats; two identical halves
   scroll exactly -50% for a seamless loop */
.marquee{
  overflow:hidden;background:var(--ink);color:var(--paper);
  font-size:var(--fs-sm);font-weight:900;text-transform:uppercase;
  letter-spacing:0.18em;white-space:nowrap;
}
.marquee-track{display:flex;width:max-content;animation:mq 28s linear infinite;}
.mq-half{display:inline-block;padding:9px 0;}
.mq-s{color:var(--accent);margin:0 var(--s5);}
@keyframes mq{to{transform:translateX(-50%);}}

/* masthead — solid paper, hard rule; no blur anywhere */
.masthead{
  position:sticky;top:0;z-index:50;
  background:var(--paper);
  border-bottom:var(--bw) solid var(--ink);
}
.masthead .wrap{
  display:flex;align-items:center;gap:var(--s4);
  min-height:78px;padding-top:var(--s3);padding-bottom:var(--s3);
  flex-wrap:nowrap;
}
.brand{display:flex;align-items:center;gap:var(--s3);flex:0 0 auto;}
.mast-spacer{flex:1 1 auto;min-width:var(--s4);}
.brand-mark{
  width:44px;height:44px;flex:0 0 auto;
  display:grid;place-items:center;
  background:var(--second);color:#000;
  border:var(--bw) solid var(--ink);box-shadow:var(--sh-sm);
  transform:rotate(-3deg);
}
.brand-mark svg{width:20px;height:20px;}
.brand-name{font-size:26px;font-weight:900;letter-spacing:-0.03em;text-transform:uppercase;color:var(--ink);}
.brand-sub{
  font-size:var(--fs-xs);font-weight:900;
  text-transform:uppercase;letter-spacing:0.14em;color:#000;
  background:var(--violet);border:3px solid var(--ink);padding:3px 10px;
  box-shadow:3px 3px 0 0 var(--ink);transform:rotate(2deg);
}

.metrics{display:flex;align-items:stretch;flex:0 0 auto;border:var(--bw) solid var(--ink);background:var(--card);box-shadow:var(--sh-sm);}
.metric{display:flex;flex-direction:column;justify-content:center;gap:2px;padding:8px var(--s4);border-left:var(--bw) solid var(--ink);min-width:0;}
.metric:first-child{border-left:none;}
.m-l{font-family:var(--font-mono);font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.14em;color:var(--ink-soft);white-space:nowrap;}
.m-v{font-family:var(--font-mono);font-size:17px;font-weight:700;color:var(--ink);line-height:1;font-variant-numeric:tabular-nums;white-space:nowrap;}
.metric.needs{background:var(--st-esc);}
.metric.needs .m-l,.metric.needs .m-v{color:#000;}

.head-actions{display:flex;gap:var(--s2);flex:0 0 auto;}
.changelog-link{flex:0 0 auto;}
.icon-btn{
  flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;
  width:42px;height:42px;padding:0;
  border:var(--bw) solid var(--ink);background:var(--card);color:var(--ink);
  box-shadow:var(--sh-sm);
  transition:transform .1s ease-out, box-shadow .1s ease-out, background .1s ease-out, color .1s ease-out;
}
.icon-btn:hover{background:var(--second);color:#000;}
.icon-btn:active{transform:translate(4px,4px);box-shadow:none;}
.icon-btn svg{width:18px;height:18px;}
.theme-toggle .ic-moon{display:none;}
:root[data-theme="dark"] .theme-toggle .ic-sun{display:none;}
:root[data-theme="dark"] .theme-toggle .ic-moon{display:inline;}

/* queue ribbon — a bordered bar of solid blocks over an ink base, so the
   4px gaps read as hard seams; the legend carries the names and counts */
main{padding:var(--s10) 0 0;}
.ribbon{
  padding:var(--s5);
  background:var(--card);border:var(--bw) solid var(--ink);box-shadow:var(--sh-md);
  transform:rotate(-0.4deg);
}
.ribbon-bar{display:flex;height:26px;gap:4px;margin-bottom:var(--s4);border:var(--bw) solid var(--ink);background:var(--ink);}
.ribbon-bar span{display:block;background:var(--d);transition:flex .2s ease-out;}
.ribbon-legend{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s2) var(--s3);}
.leg{
  display:inline-flex;align-items:center;gap:8px;
  font-size:var(--fs-sm);font-weight:700;text-transform:uppercase;letter-spacing:0.05em;
  color:var(--ink);background:var(--paper);border:2px solid var(--ink);
  padding:3px 10px 3px 6px;box-shadow:2px 2px 0 0 var(--ink);
}
.leg-dot{width:14px;height:14px;flex:0 0 auto;background:var(--d);border:2px solid var(--ink);}
.leg-name{font-weight:700;}
.leg-n{font-family:var(--font-mono);font-size:var(--fs-xs);font-weight:700;color:var(--ink);font-variant-numeric:tabular-nums;}
.leg-z{opacity:0.35;box-shadow:none;}

/* sections — a rotated label pill, then a massive display title */
.sec{padding:var(--s12) 0 var(--s8);scroll-margin-top:96px;}
.sec-head{margin-bottom:var(--s8);}
.sec-head-row{display:flex;align-items:center;gap:var(--s4);flex-wrap:wrap;}
.sec-label{
  display:inline-block;border-radius:999px;
  font-size:var(--fs-sm);font-weight:900;text-transform:uppercase;letter-spacing:0.12em;
  color:#000;background:var(--lbl, var(--second));
  border:3px solid var(--ink);padding:5px 16px;
  box-shadow:3px 3px 0 0 var(--ink);transform:rotate(-1.5deg);
}
.sec-count{
  font-size:var(--fs-md);font-weight:900;color:#000;
  background:var(--second);border:3px solid var(--ink);padding:3px 13px;
  box-shadow:3px 3px 0 0 var(--ink);transform:rotate(2deg);
}
.star{width:34px;height:34px;color:var(--ink);animation:spin-slow 12s linear infinite;}
@keyframes spin-slow{from{transform:rotate(0);}to{transform:rotate(360deg);}}
.sec-title{
  margin-top:var(--s5);
  font-size:var(--fs-display);font-weight:900;text-transform:uppercase;
  letter-spacing:-0.03em;line-height:0.88;color:var(--ink);
}
.hollow{color:transparent;-webkit-text-stroke:3px var(--ink);}
@supports not (-webkit-text-stroke:3px black){ .hollow{color:var(--accent);} }
.sec-title.boxed{
  display:inline-block;font-size:var(--fs-boxed);
  background:var(--card);border:var(--bw) solid var(--ink);
  padding:10px 26px 12px;box-shadow:var(--sh-lg);transform:rotate(-1deg);
}
.sec-sub{margin-top:var(--s4);font-size:var(--fs-md);font-weight:700;color:var(--ink);max-width:60ch;}

/* full-bleed color blocking */
.band{border-top:var(--bw) solid #000;border-bottom:var(--bw) solid #000;}
.band-violet{
  /* a printed light island: identical in both themes */
  --paper:#FFFDF5; --card:#FFFFFF; --ink:#000000;
  --ink-soft:rgba(0,0,0,0.6); --grid-line:rgba(0,0,0,0.08);
  color:#000;
  background-color:var(--violet);
  background-image:radial-gradient(rgba(0,0,0,0.16) 1.5px, transparent 1.5px);
  background-size:18px 18px;
  margin-top:var(--s10);
  padding-bottom:var(--s16);
}

/* needs-you zone — a red halftone slab the escalated cards sit on */
.needs-zone{
  position:relative;padding:var(--s4);
  background-color:var(--st-esc);
  background-image:radial-gradient(rgba(0,0,0,0.28) 1.5px, transparent 1.5px);
  background-size:14px 14px;
  border:var(--bw) solid var(--ink);box-shadow:var(--sh-lg);
}
.needs-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:var(--s4);}
@media (max-width:520px){ .needs-grid{grid-template-columns:1fr;} }

.allclear{
  display:flex;align-items:center;gap:var(--s6);
  padding:var(--s6) var(--s8);
  background:var(--st-don);color:#000;
  border:var(--bw) solid var(--ink);box-shadow:var(--sh-md);
  transform:rotate(-0.4deg);
}
.allclear-ic{
  width:60px;height:60px;flex:0 0 auto;display:grid;place-items:center;
  background:var(--card);color:var(--ink);
  border:var(--bw) solid var(--ink);box-shadow:var(--sh-sm);transform:rotate(3deg);
}
.allclear-ic svg{width:30px;height:30px;}
.allclear h3{font-size:var(--fs-lg);font-weight:900;text-transform:uppercase;letter-spacing:0.02em;}
.allclear p{font-size:var(--fs-md);font-weight:700;margin-top:3px;}

/* filters — chips and segments click down like switches */
.filters{
  display:flex;align-items:center;justify-content:space-between;gap:var(--s5);
  flex-wrap:wrap;margin-bottom:var(--s8);
}
.chips{display:flex;gap:var(--s3);flex-wrap:wrap;}
.chip{
  display:inline-flex;align-items:center;gap:var(--s2);height:40px;padding:0 var(--s4);
  border:3px solid var(--ink);background:var(--card);
  color:var(--ink);font-size:var(--fs-sm);font-weight:900;
  text-transform:uppercase;letter-spacing:0.05em;box-shadow:var(--sh-sm);
  transition:transform .1s ease-out, box-shadow .1s ease-out, background .1s ease-out, color .1s ease-out;
}
.chip:hover{background:var(--second);color:#000;}
.chip:active{transform:translate(4px,4px);box-shadow:none;}
.chip[aria-pressed="true"]{background:var(--ink);color:var(--paper);transform:translate(2px,2px);box-shadow:2px 2px 0 0 var(--ink);}
.chip .cc{font-family:var(--font-mono);font-size:var(--fs-xs);font-weight:700;}

.segmented{display:inline-flex;padding:0;gap:0;background:var(--card);border:3px solid var(--ink);box-shadow:var(--sh-sm);}
.seg{
  height:40px;padding:0 var(--s4);
  font-size:var(--fs-sm);font-weight:900;text-transform:uppercase;letter-spacing:0.05em;
  color:var(--ink);transition:background .1s ease-out, color .1s ease-out;white-space:nowrap;
}
.seg + .seg{border-left:3px solid var(--ink);}
.seg:hover{background:var(--second);color:#000;}
.seg[aria-pressed="true"]{background:var(--ink);color:var(--paper);}

/* project card — a bordered panel with a status spine, lifting on hover */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:var(--s8);}
@media (max-width:380px){ .grid{grid-template-columns:1fr;} }

.card{
  position:relative;display:flex;flex-direction:column;
  background:var(--card);border:var(--bw) solid var(--ink);box-shadow:var(--sh-md);
  padding:var(--s5) var(--s5) var(--s4) calc(var(--s5) + 14px);
  overflow:hidden;
  transition:transform .15s ease-out, box-shadow .15s ease-out;
}
.card::before{
  content:"";position:absolute;left:0;top:0;bottom:0;width:10px;
  background:var(--s);border-right:var(--bw) solid var(--ink);
}
.card:hover{transform:translateY(-5px);box-shadow:var(--sh-lg);}

.card.hero{
  padding:var(--s6) var(--s6) var(--s5) calc(var(--s6) + 14px);
}

.card-top{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--s3);}
.card-id{min-width:0;}
.card-name{font-size:var(--fs-lg);font-weight:900;text-transform:uppercase;color:var(--ink);letter-spacing:-0.01em;line-height:var(--lh-tight);display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap;}
.hero .card-name{font-size:var(--fs-xl);}
.card-desc{font-size:var(--fs-base);font-weight:500;color:var(--ink);margin-top:var(--s2);line-height:var(--lh-snug);text-wrap:pretty;}

.pill{
  display:inline-flex;align-items:center;gap:7px;height:28px;padding:0 11px;
  font-size:var(--fs-xs);font-weight:900;text-transform:uppercase;letter-spacing:0.06em;
  background:var(--s);color:#000;white-space:nowrap;flex:0 0 auto;
  border:3px solid var(--ink);box-shadow:3px 3px 0 0 var(--ink);
  transform:rotate(2deg);
}
.pill .dot{width:8px;height:8px;background:#000;}
.pill.running .dot{animation:blink 1s steps(2, jump-none) infinite;}
@keyframes blink{0%,100%{opacity:1;}50%{opacity:0.15;}}

.badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:var(--s4);}
.badge{
  display:inline-flex;align-items:center;gap:5px;height:25px;padding:0 9px;
  font-size:var(--fs-xs);font-weight:700;font-family:var(--font-mono);
  text-transform:uppercase;letter-spacing:0.04em;
  background:var(--paper);color:var(--ink);border:2px solid var(--ink);white-space:nowrap;
}
.badge svg{width:11px;height:11px;}
.badge.auto{background:var(--st-rev);color:#000;transform:rotate(-2deg);box-shadow:2px 2px 0 0 var(--ink);}
.badge.blocked{background:var(--st-esc);color:#000;box-shadow:2px 2px 0 0 var(--ink);}
.badge.stale,.badge.fresh-stale{background:var(--st-stale);color:#000;}

.blocked-line{
  display:flex;align-items:flex-start;gap:var(--s2);margin-top:var(--s3);
  padding:var(--s3);
  background:var(--st-esc);color:#000;border:3px solid var(--ink);
  box-shadow:3px 3px 0 0 var(--ink);transform:rotate(-0.5deg);
  font-size:var(--fs-base);font-weight:500;line-height:var(--lh-snug);
}
.blocked-line svg{width:15px;height:15px;flex:0 0 auto;margin-top:2px;}
.blocked-line b{font-weight:900;text-transform:uppercase;font-size:var(--fs-xs);letter-spacing:0.04em;}

.fired-line{
  display:flex;align-items:center;gap:6px;margin-top:var(--s3);
  font-family:var(--font-mono);font-size:var(--fs-xs);font-weight:700;color:var(--ink-soft);
}
.fired-line svg{width:12px;height:12px;flex:0 0 auto;color:var(--ink);}

.log{display:flex;flex-direction:column;gap:var(--s2);margin-top:var(--s2);}
.log-row{display:flex;gap:var(--s3);font-size:var(--fs-base);line-height:var(--lh-snug);}
.log-date{font-family:var(--font-mono);font-size:var(--fs-xs);font-weight:700;color:var(--ink-soft);flex:0 0 auto;width:42px;padding-top:1px;letter-spacing:-0.02em;}
.log-text{color:var(--ink);font-weight:500;text-wrap:pretty;}
.log-cap{
  display:inline-block;align-self:flex-start;
  font-size:var(--fs-xs);font-family:var(--font-mono);font-weight:700;
  text-transform:uppercase;letter-spacing:0.08em;color:#000;
  background:var(--second);border:2px solid var(--ink);padding:2px 8px;
  box-shadow:2px 2px 0 0 var(--ink);transform:rotate(-1deg);
  margin-top:var(--s4);margin-bottom:var(--s2);
}

.expander{margin-top:var(--s2);}
.exp-body{display:none;}
.card[data-open="true"] .exp-body{display:block;animation:pop .12s ease-out;}
@keyframes pop{from{opacity:0;transform:translateY(-3px);}to{opacity:1;transform:none;}}
.prompt-cap{display:flex;align-items:center;justify-content:space-between;gap:var(--s3);margin-top:var(--s4);margin-bottom:var(--s2);}
.prompt{
  font-family:var(--font-mono);font-size:var(--fs-sm);line-height:1.6;
  color:var(--ink);background:var(--paper);border:3px solid var(--ink);
  box-shadow:3px 3px 0 0 var(--ink);
  padding:var(--s4);white-space:pre-wrap;text-wrap:pretty;
}

.card-foot{display:flex;align-items:center;gap:var(--s3);margin-top:auto;padding-top:var(--s5);}
.exp-toggle{
  display:inline-flex;align-items:center;gap:6px;height:36px;padding:0 var(--s3);white-space:nowrap;
  color:var(--ink);font-size:var(--fs-xs);font-weight:900;
  text-transform:uppercase;letter-spacing:0.05em;
  border:3px solid transparent;
  transition:background .1s ease-out, border-color .1s ease-out, box-shadow .1s ease-out, transform .1s ease-out;
}
.exp-toggle:hover{border-color:var(--ink);background:var(--paper);box-shadow:3px 3px 0 0 var(--ink);}
.exp-toggle:active{transform:translate(3px,3px);box-shadow:none;}
.exp-toggle svg{width:14px;height:14px;transition:transform .15s ease-out;}
.card[data-open="true"] .exp-toggle svg{transform:rotate(180deg);}
.foot-spacer{flex:1 1 auto;}

.btn-copy{
  display:inline-flex;align-items:center;gap:8px;height:42px;padding:0 var(--s4);white-space:nowrap;
  font-size:var(--fs-sm);font-weight:900;text-transform:uppercase;letter-spacing:0.05em;
  background:var(--second);color:#000;border:var(--bw) solid var(--ink);
  box-shadow:var(--sh-sm);
  transition:transform .1s ease-out, box-shadow .1s ease-out, background .1s ease-out;
}
.btn-copy svg{width:15px;height:15px;}
.btn-copy:hover{background:var(--accent);}
.btn-copy:active{transform:translate(4px,4px);box-shadow:none;}
.btn-copy.copied{background:var(--st-don);color:#000;}
.btn-copy .ic-check{display:none;}
.btn-copy.copied .ic-copy{display:none;}
.btn-copy.copied .ic-check{display:inline;}

.btn-copy-ghost{
  display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 var(--s3);
  font-size:var(--fs-xs);font-weight:700;font-family:var(--font-mono);
  text-transform:uppercase;letter-spacing:0.04em;
  border:2px solid var(--ink);background:var(--card);color:var(--ink);
  box-shadow:2px 2px 0 0 var(--ink);
  transition:transform .1s ease-out, box-shadow .1s ease-out, background .1s ease-out, color .1s ease-out;
}
.btn-copy-ghost svg{width:12px;height:12px;}
.btn-copy-ghost:hover{background:var(--second);color:#000;}
.btn-copy-ghost:active{transform:translate(2px,2px);box-shadow:none;}
.btn-copy-ghost.copied{background:var(--st-don);color:#000;}
.btn-copy-ghost .ic-check{display:none;}
.btn-copy-ghost.copied .ic-copy{display:none;}
.btn-copy-ghost.copied .ic-check{display:inline;}

.empty{
  grid-column:1/-1;text-align:center;padding:var(--s12) var(--s6);
  color:var(--ink);font-size:var(--fs-md);font-weight:900;
  text-transform:uppercase;letter-spacing:0.05em;
  border:4px dashed var(--ink);background:var(--card);
}

.card.is-hidden{display:none;}

/* changelog timeline — a hard ink spine with square nodes and
   status-colored project stickers, printed on the violet band */
.timeline{position:relative;padding-top:var(--s2);}
.day{position:relative;padding-left:var(--s10);margin-bottom:var(--s8);}
.day::before{
  content:"";position:absolute;left:8px;top:24px;bottom:-32px;width:5px;background:var(--ink);
}
.day:last-child::before{display:none;}
.day-node{
  position:absolute;left:0;top:2px;width:21px;height:21px;
  background:var(--second);border:3px solid var(--ink);
  box-shadow:3px 3px 0 0 var(--ink);transform:rotate(-4deg);
}
.day:first-child .day-node{background:var(--accent);}
.day-head{display:flex;align-items:baseline;gap:var(--s3);margin-bottom:var(--s3);}
.day-label{font-size:var(--fs-xl);font-weight:900;text-transform:uppercase;letter-spacing:-0.01em;color:var(--ink);}
.day-date{font-family:var(--font-mono);font-size:var(--fs-xs);font-weight:700;color:var(--ink-soft);letter-spacing:0.02em;}
.entry{
  display:grid;grid-template-columns:auto 130px 1fr;align-items:start;
  column-gap:var(--s3);padding:var(--s2) var(--s3);
  border:3px solid transparent;
  transition:background .1s ease-out, border-color .1s ease-out, box-shadow .1s ease-out;
}
.entry:hover{background:var(--card);border-color:var(--ink);box-shadow:var(--sh-sm);}
.entry + .entry{margin-top:5px;}
.entry-dot{width:11px;height:11px;background:var(--s);border:2px solid var(--ink);flex:0 0 auto;margin-top:5px;}
.entry-proj{
  font-size:var(--fs-xs);font-weight:700;font-family:var(--font-mono);
  text-transform:uppercase;letter-spacing:0.02em;color:#000;
  background:var(--s);border:2px solid var(--ink);padding:2px 8px;
  box-shadow:2px 2px 0 0 var(--ink);
  white-space:nowrap;justify-self:start;transform:rotate(-1.5deg);
}
.entry-text{font-size:var(--fs-base);font-weight:500;color:var(--ink);line-height:var(--lh-snug);}

/* footer — an inverted band */
footer.foot{
  background:var(--ink);color:var(--paper);
  padding:var(--s6) 0;
}
footer.foot .wrap{display:flex;gap:var(--s4);align-items:center;flex-wrap:wrap;}
.foot-brand{font-size:var(--fs-xl);font-weight:900;text-transform:uppercase;letter-spacing:-0.02em;color:var(--accent);}
.foot-tag{font-size:var(--fs-sm);font-weight:700;text-transform:uppercase;letter-spacing:0.08em;}
.foot-gen{font-size:var(--fs-xs);font-weight:700;opacity:0.7;}
footer.foot .foot-spacer{flex:1 1 auto;}

@media (max-width:820px){
  .metrics{display:none;}
}
@media (max-width:680px){
  .wrap{padding:0 var(--s4);}
  .sec{padding:var(--s8) 0 var(--s6);}
}
@media (prefers-reduced-motion: reduce){
  *{transition:none !important;animation:none !important;}
}
"""

HEAD_CLOSE = """</style>
</head>
<body>
"""

SCRIPT = """
<script>
(function(){
  "use strict";
  var root = document.documentElement;

  /* ---- theme toggle (light is the default; dark is opt-in) ---- */
  var KEY = "legwork-theme";
  var toggle = document.getElementById("themeToggle");
  function effective(){ return root.getAttribute("data-theme") === "dark" ? "dark" : "light"; }
  function syncLabel(){ var e = effective(); if(toggle) toggle.setAttribute("aria-label", (e === "dark" ? "Dark" : "Light") + " theme — switch to " + (e === "dark" ? "light" : "dark")); }
  try{ var saved = localStorage.getItem(KEY); if(saved === "dark" || saved === "light"){ root.setAttribute("data-theme", saved); } }catch(e){}
  syncLabel();
  if(toggle){
    toggle.addEventListener("click", function(){
      var next = effective() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try{ localStorage.setItem(KEY, next); }catch(e){}
      syncLabel();
    });
  }

  /* ---- expanders ---- */
  document.querySelectorAll(".exp-toggle").forEach(function(btn){
    btn.addEventListener("click", function(){
      var card = btn.closest(".card");
      var open = card.getAttribute("data-open") === "true";
      card.setAttribute("data-open", open ? "false" : "true");
      var lab = btn.querySelector(".exp-label");
      if(lab) lab.textContent = open ? "Prompt & log" : "Hide";
    });
  });

  /* ---- copy prompt ---- */
  function flash(btn){
    btn.classList.add("copied");
    var lab = btn.querySelector(".copy-label");
    var prev = lab ? lab.textContent : null;
    if(lab) lab.textContent = btn.classList.contains("btn-copy") ? "Copied" : "copied";
    setTimeout(function(){ btn.classList.remove("copied"); if(lab) lab.textContent = prev; }, 1600);
  }
  document.querySelectorAll(".copy-src").forEach(function(btn){
    btn.addEventListener("click", function(){
      var card = btn.closest(".card");
      var text = card ? (card.getAttribute("data-prompt") || "") : "";
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(function(){ flash(btn); }).catch(function(){ legacyCopy(text); flash(btn); });
      } else { legacyCopy(text); flash(btn); }
    });
  });
  function legacyCopy(text){
    var ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly",""); ta.style.position="absolute"; ta.style.left="-9999px";
    document.body.appendChild(ta); ta.select();
    try{ document.execCommand("copy"); }catch(e){}
    document.body.removeChild(ta);
  }

  /* ---- filters ---- */
  var STATES = {
    everything: ["escalated","queued","running","review","done","icebox"],
    actionable: ["escalated","queued","running","review"],
    done: ["done"],
    icebox: ["icebox"]
  };
  var curCat = "all", curState = "everything";
  var CAT_KEY = "legwork-cat", STATE_KEY = "legwork-state", SCROLL_KEY = "legwork-scroll";
  var cards = Array.prototype.slice.call(document.querySelectorAll("#grid .card"));
  var heroCards = Array.prototype.slice.call(document.querySelectorAll("#needsZone .card"));
  var needsSec = document.getElementById("needs");
  var emptyState = document.getElementById("emptyState");
  var needsCount = document.getElementById("needsCount");

  function apply(){
    var allowed = STATES[curState];
    var visible = 0;
    cards.forEach(function(c){
      var okCat = curCat === "all" || c.getAttribute("data-category") === curCat;
      var okState = allowed.indexOf(c.getAttribute("data-status")) !== -1;
      var show = okCat && okState;
      c.classList.toggle("is-hidden", !show);
      if(show) visible++;
    });
    if(emptyState) emptyState.hidden = visible > 0;

    /* needs-you zone shows only when escalated is in the active state set */
    var escAllowed = allowed.indexOf("escalated") !== -1;
    if(heroCards.length === 0){
      /* all-clear panel: show under states that include escalations */
      if(needsSec) needsSec.style.display = escAllowed ? "" : "none";
      if(needsCount) needsCount.textContent = "0";
      return;
    }
    var heroVisible = 0;
    heroCards.forEach(function(c){
      var okCat = curCat === "all" || c.getAttribute("data-category") === curCat;
      var show = escAllowed && okCat;
      c.classList.toggle("is-hidden", !show);
      if(show) heroVisible++;
    });
    if(needsSec) needsSec.style.display = heroVisible > 0 ? "" : "none";
    if(needsCount) needsCount.textContent = heroVisible;
  }

  var catFilter = document.getElementById("catFilter");
  if(catFilter) catFilter.addEventListener("click", function(e){
    var b = e.target.closest(".chip"); if(!b) return;
    curCat = b.getAttribute("data-cat");
    try{ localStorage.setItem(CAT_KEY, curCat); }catch(e){}
    this.querySelectorAll(".chip").forEach(function(x){ x.setAttribute("aria-pressed", x===b ? "true":"false"); });
    apply();
  });
  var stateFilter = document.getElementById("stateFilter");
  if(stateFilter) stateFilter.addEventListener("click", function(e){
    var b = e.target.closest(".seg"); if(!b) return;
    curState = b.getAttribute("data-state");
    try{ localStorage.setItem(STATE_KEY, curState); }catch(e){}
    this.querySelectorAll(".seg").forEach(function(x){ x.setAttribute("aria-pressed", x===b ? "true":"false"); });
    apply();
  });

  /* ---- restore UI state across the 120s auto-refresh ---- */
  function press(group, attr, val){
    if(!group) return;
    group.querySelectorAll("[" + attr + "]").forEach(function(x){
      x.setAttribute("aria-pressed", x.getAttribute(attr) === val ? "true" : "false");
    });
  }
  try{
    var savedCat = localStorage.getItem(CAT_KEY);
    if(savedCat && catFilter && catFilter.querySelector('[data-cat="' + savedCat + '"]')){
      curCat = savedCat; press(catFilter, "data-cat", curCat);
    }
    var savedState = localStorage.getItem(STATE_KEY);
    if(savedState && STATES[savedState]){
      curState = savedState; press(stateFilter, "data-state", curState);
    }
  }catch(e){}

  apply();

  /* restore scroll last, after apply() has settled the layout */
  try{
    var y = parseInt(localStorage.getItem(SCROLL_KEY), 10);
    if(!isNaN(y)) window.scrollTo(0, y);
  }catch(e){}
  var scrollTick = false;
  window.addEventListener("scroll", function(){
    if(scrollTick) return;
    scrollTick = true;
    setTimeout(function(){
      scrollTick = false;
      try{ localStorage.setItem(SCROLL_KEY, String(window.scrollY)); }catch(e){}
    }, 200);
  }, {passive:true});
})();
</script>
</body>
</html>
"""


def main():
    if not PROJECTS_DIR.is_dir():
        sys.exit(f"No projects directory at {PROJECTS_DIR}")
    projects = []
    for path in sorted(PROJECTS_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        parsed = parse_project(path)
        if parsed:
            projects.append(parsed)
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(build(projects), encoding="utf-8")
    print(f"Wrote {OUT_FILE} ({len(projects)} projects)")


if __name__ == "__main__":
    main()
