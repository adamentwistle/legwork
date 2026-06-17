#!/usr/bin/env python3
"""Build dashboard/index.html from projects/*.md.

Zero dependencies. Run from anywhere:

    python3 scripts/build_dashboard.py

Reads simple frontmatter (key: value lines between --- markers), the first
fenced block under '## Next prompt', and bullet lines under '## Log'.
The HTML is a build artifact: every run replaces it wholesale. All styling
lives in CSS below; project data never lives in this file.

The visual design (tokens, components, the instrument-style masthead, the
queue-distribution ribbon, the status-spined cards and the changelog
timeline) comes from a Claude Design handoff. The CSS and JS are lifted
verbatim; only the data binding lives here.
"""

import html
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from legwork_common import parse_frontmatter, PROMPT_RE, parse_date

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
    text = path.read_text(encoding="utf-8")
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

    # Freshness is the newest of the frontmatter date and the latest log
    # entry, so a forgotten `updated` bump cannot make a project look stale.
    candidates = [parse_date(meta.get("updated", ""))]
    if log_lines:
        candidates.append(parse_date(log_lines[0]))
    dates = [c for c in candidates if c]
    days_quiet = (date.today() - max(dates)).days if dates else None

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


# A completed line looks like "... completed <fname>: exit 0, 7 min $1.23 ...";
# the dollar cost is the runner's per-fire spend.
COST_RE = re.compile(r"\$(\d+\.\d{2})")


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

    body = f"""
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

<main class="wrap">

  <section class="ribbon" aria-label="Queue distribution">
    <div class="ribbon-bar" role="img" aria-label="Project count by status">{ribbon_bar}</div>
    <div class="ribbon-legend">{ribbon_legend}</div>
  </section>

  <section class="sec" id="needs">
    <div class="sec-head">
      <h2 class="sec-title">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--st-esc)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>
        Needs you
      </h2>
      <span class="sec-count mono" id="needsCount">{len(escalated)}</span>
      <span class="sec-sub">Escalated by the reviewer &mdash; these are blocked on a human.</span>
    </div>
    {needs_body}
  </section>

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

  <section class="sec">
    <div class="grid" id="grid">
      {grid}
      <div class="empty" id="emptyState" hidden>No projects match this filter.</div>
    </div>
  </section>

  <section class="sec" id="changelog">
    <div class="sec-head">
      <h2 class="sec-title">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--st-run)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.4 2.6L3 8"/><path d="M3 4v4h4"/><path d="M12 8v4l3 2"/></svg>
        Changelog
      </h2>
      <span class="sec-sub">Every dated entry across the queue &mdash; newest first.</span>
    </div>
    <div class="timeline">
      {changelog_html(projects)}
    </div>
  </section>

  <footer class="foot">
    <span class="mono">legwork</span>
    <span style="opacity:.4">&middot;</span>
    <span>autonomous project queue for Claude Code</span>
    <span style="opacity:.4">&middot;</span>
    <span class="mono">generated {generated}</span>
  </footer>

</main>
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
   LEGWORK — design tokens (from Claude Design handoff)
   ============================================================ */
:root{
  --font-sans:'Geist', system-ui, -apple-system, sans-serif;
  --font-mono:'Geist Mono', ui-monospace, 'SF Mono', Menlo, monospace;
  --fs-xs:11px; --fs-sm:12px; --fs-base:13px; --fs-md:14px; --fs-lg:16px;
  --fs-xl:20px; --fs-2xl:28px; --fs-3xl:38px;
  --lh-tight:1.15; --lh-snug:1.35; --lh-normal:1.55;

  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:20px; --s6:24px;
  --s8:32px; --s10:40px; --s12:48px; --s16:64px;

  --r-sm:0; --r-md:0; --r-lg:0; --r-xl:0; --r-pill:0;

  /* neutrals — LIGHT */
  --bg:oklch(0.985 0.003 255);
  --bg-grad:oklch(0.965 0.004 255);
  --surface:oklch(1 0 0);
  --surface-2:oklch(0.975 0.003 255);
  --surface-3:oklch(0.955 0.004 255);
  --border:oklch(0.915 0.005 255);
  --border-strong:oklch(0.86 0.006 255);
  --text-strong:oklch(0.24 0.012 265);
  --text:oklch(0.40 0.011 265);
  --text-muted:oklch(0.56 0.009 265);
  --text-faint:oklch(0.68 0.008 265);

  --sh-sm:0 1px 2px oklch(0.2 0.02 265 / 0.04), 0 1px 1px oklch(0.2 0.02 265 / 0.03);
  --sh-md:0 2px 4px oklch(0.2 0.02 265 / 0.05), 0 4px 12px oklch(0.2 0.02 265 / 0.05);
  --sh-lg:0 8px 24px oklch(0.2 0.02 265 / 0.08), 0 2px 6px oklch(0.2 0.02 265 / 0.05);
  --ring:oklch(0.58 0.15 250);

  /* status — LIGHT  (solid / tint-bg / text-fg) */
  --st-esc:oklch(0.58 0.21 26);   --st-esc-bg:oklch(0.955 0.045 30);  --st-esc-fg:oklch(0.50 0.19 28);
  --st-que:oklch(0.56 0.16 252);  --st-que-bg:oklch(0.955 0.035 252); --st-que-fg:oklch(0.47 0.16 254);
  --st-run:oklch(0.69 0.155 72);  --st-run-bg:oklch(0.955 0.055 80);  --st-run-fg:oklch(0.52 0.13 62);
  --st-rev:oklch(0.57 0.18 302);  --st-rev-bg:oklch(0.955 0.04 305);  --st-rev-fg:oklch(0.50 0.17 302);
  --st-don:oklch(0.61 0.14 158);  --st-don-bg:oklch(0.95 0.045 162);  --st-don-fg:oklch(0.45 0.12 160);
  --st-ice:oklch(0.62 0.028 240); --st-ice-bg:oklch(0.955 0.006 240); --st-ice-fg:oklch(0.49 0.018 240);
  --st-stale:oklch(0.66 0.14 60); --st-stale-bg:oklch(0.96 0.05 70);  --st-stale-fg:oklch(0.50 0.12 55);

  --shimmer:oklch(1 0 0 / 0.6);
}

/* legacy browsers without oklch/color-mix — keep the page legible */
@supports not (color: oklch(0 0 0)){
  body{ background:#fafafa; color:#3a3a42; }
}

@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){ color-scheme:dark;
    --bg:oklch(0.165 0.012 265);
    --bg-grad:oklch(0.20 0.015 265);
    --surface:oklch(0.205 0.013 265);
    --surface-2:oklch(0.235 0.014 265);
    --surface-3:oklch(0.27 0.015 265);
    --border:oklch(0.29 0.013 265);
    --border-strong:oklch(0.37 0.015 265);
    --text-strong:oklch(0.97 0.005 265);
    --text:oklch(0.80 0.008 265);
    --text-muted:oklch(0.63 0.01 265);
    --text-faint:oklch(0.52 0.011 265);
    --sh-sm:0 1px 2px oklch(0 0 0 / 0.3);
    --sh-md:0 2px 8px oklch(0 0 0 / 0.36), 0 1px 2px oklch(0 0 0 / 0.3);
    --sh-lg:0 12px 34px oklch(0 0 0 / 0.5), 0 2px 8px oklch(0 0 0 / 0.4);
    --ring:oklch(0.70 0.14 252);
    --st-esc:oklch(0.69 0.20 28);   --st-esc-bg:oklch(0.31 0.085 28);  --st-esc-fg:oklch(0.83 0.13 32);
    --st-que:oklch(0.71 0.145 252); --st-que-bg:oklch(0.30 0.065 254); --st-que-fg:oklch(0.84 0.10 252);
    --st-run:oklch(0.79 0.155 76);  --st-run-bg:oklch(0.33 0.075 75);  --st-run-fg:oklch(0.87 0.13 82);
    --st-rev:oklch(0.71 0.165 302); --st-rev-bg:oklch(0.31 0.075 302); --st-rev-fg:oklch(0.85 0.12 302);
    --st-don:oklch(0.73 0.145 160); --st-don-bg:oklch(0.30 0.065 162); --st-don-fg:oklch(0.84 0.12 162);
    --st-ice:oklch(0.69 0.03 240);  --st-ice-bg:oklch(0.285 0.012 240);--st-ice-fg:oklch(0.80 0.022 240);
    --st-stale:oklch(0.78 0.14 70); --st-stale-bg:oklch(0.32 0.07 68); --st-stale-fg:oklch(0.86 0.12 75);
    --shimmer:oklch(1 0 0 / 0.16);
  }
}
:root[data-theme="dark"]{ color-scheme:dark;
  --bg:oklch(0.165 0.012 265);
  --bg-grad:oklch(0.20 0.015 265);
  --surface:oklch(0.205 0.013 265);
  --surface-2:oklch(0.235 0.014 265);
  --surface-3:oklch(0.27 0.015 265);
  --border:oklch(0.29 0.013 265);
  --border-strong:oklch(0.37 0.015 265);
  --text-strong:oklch(0.97 0.005 265);
  --text:oklch(0.80 0.008 265);
  --text-muted:oklch(0.63 0.01 265);
  --text-faint:oklch(0.52 0.011 265);
  --sh-sm:0 1px 2px oklch(0 0 0 / 0.3);
  --sh-md:0 2px 8px oklch(0 0 0 / 0.36), 0 1px 2px oklch(0 0 0 / 0.3);
  --sh-lg:0 12px 34px oklch(0 0 0 / 0.5), 0 2px 8px oklch(0 0 0 / 0.4);
  --ring:oklch(0.70 0.14 252);
  --st-esc:oklch(0.69 0.20 28);   --st-esc-bg:oklch(0.31 0.085 28);  --st-esc-fg:oklch(0.83 0.13 32);
  --st-que:oklch(0.71 0.145 252); --st-que-bg:oklch(0.30 0.065 254); --st-que-fg:oklch(0.84 0.10 252);
  --st-run:oklch(0.79 0.155 76);  --st-run-bg:oklch(0.33 0.075 75);  --st-run-fg:oklch(0.87 0.13 82);
  --st-rev:oklch(0.71 0.165 302); --st-rev-bg:oklch(0.31 0.075 302); --st-rev-fg:oklch(0.85 0.12 302);
  --st-don:oklch(0.73 0.145 160); --st-don-bg:oklch(0.30 0.065 162); --st-don-fg:oklch(0.84 0.12 162);
  --st-ice:oklch(0.69 0.03 240);  --st-ice-bg:oklch(0.285 0.012 240);--st-ice-fg:oklch(0.80 0.022 240);
  --st-stale:oklch(0.78 0.14 70); --st-stale-bg:oklch(0.32 0.07 68); --st-stale-fg:oklch(0.86 0.12 75);
  --shimmer:oklch(1 0 0 / 0.16);
}

.s-esc{--s:var(--st-esc);--s-bg:var(--st-esc-bg);--s-fg:var(--st-esc-fg);}
.s-que{--s:var(--st-que);--s-bg:var(--st-que-bg);--s-fg:var(--st-que-fg);}
.s-run{--s:var(--st-run);--s-bg:var(--st-run-bg);--s-fg:var(--st-run-fg);}
.s-rev{--s:var(--st-rev);--s-bg:var(--st-rev-bg);--s-fg:var(--st-rev-fg);}
.s-don{--s:var(--st-don);--s-bg:var(--st-don-bg);--s-fg:var(--st-don-fg);}
.s-ice{--s:var(--st-ice);--s-bg:var(--st-ice-bg);--s-fg:var(--st-ice-fg);}

/* base */
*{box-sizing:border-box;}
*::selection{background:var(--st-que);color:#fff;}
html{-webkit-text-size-adjust:100%;}
body{
  margin:0; font-family:var(--font-sans); font-size:var(--fs-base);
  line-height:var(--lh-normal); color:var(--text);
  background:
    radial-gradient(1200px 600px at 80% -10%, var(--bg-grad), transparent 70%),
    var(--bg);
  background-attachment:fixed;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
  font-feature-settings:"cv11","ss01";
}
h1,h2,h3,p{margin:0;}
a{color:inherit;text-decoration:none;}
button{font-family:inherit;cursor:pointer;border:none;background:none;color:inherit;}
.mono{font-family:var(--font-mono);font-variant-numeric:tabular-nums;}
.wrap{max-width:1240px;margin:0 auto;padding:0 var(--s6);}
:focus-visible{outline:2px solid var(--ring);outline-offset:2px;border-radius:var(--r-sm);}

/* masthead */
.masthead{
  position:sticky;top:0;z-index:50;
  background:color-mix(in oklch, var(--bg) 82%, transparent);
  backdrop-filter:saturate(1.4) blur(14px);
  -webkit-backdrop-filter:saturate(1.4) blur(14px);
  border-bottom:1px solid var(--border);
}
.masthead .wrap{
  display:flex;align-items:center;gap:var(--s4);
  min-height:60px;padding-top:var(--s3);padding-bottom:var(--s3);
  flex-wrap:nowrap;
}
.brand{display:flex;align-items:center;gap:var(--s3);flex:0 0 auto;}
.mast-spacer{flex:1 1 auto;min-width:var(--s4);}
.brand-mark{
  width:30px;height:30px;flex:0 0 auto;
  display:grid;place-items:center;
  background:linear-gradient(160deg, var(--text-strong), oklch(0.40 0.04 265));
  color:var(--surface);box-shadow:var(--sh-sm);
}
:root[data-theme="dark"] .brand-mark,
:root:not([data-theme="light"]) .brand-mark{ background:linear-gradient(160deg, oklch(0.92 0.01 265), oklch(0.7 0.02 265)); color:#111; }
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]) .brand-mark{ background:linear-gradient(160deg, oklch(0.92 0.01 265), oklch(0.7 0.02 265)); color:#111; } }
.brand-name{font-size:var(--fs-xl);font-weight:600;letter-spacing:-0.03em;color:var(--text-strong);}
.brand-sub{font-family:var(--font-mono);font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:0.12em;color:var(--text-faint);padding-left:var(--s2);border-left:1px solid var(--border);margin-left:var(--s1);}

.metrics{display:flex;align-items:stretch;flex:0 0 auto;border:1px solid var(--border);background:var(--surface);box-shadow:var(--sh-sm);}
.metric{display:flex;flex-direction:column;justify-content:center;gap:2px;padding:6px var(--s4);border-left:1px solid var(--border);min-width:0;}
.metric:first-child{border-left:none;}
.m-l{font-family:var(--font-mono);font-size:9px;text-transform:uppercase;letter-spacing:0.14em;color:var(--text-faint);white-space:nowrap;}
.m-v{font-family:var(--font-mono);font-size:var(--fs-md);font-weight:600;color:var(--text-strong);line-height:1;font-variant-numeric:tabular-nums;white-space:nowrap;}
.metric.needs .m-v{color:var(--st-esc-fg);}
.metric.needs{background:var(--st-esc-bg);border-left-color:color-mix(in oklch,var(--st-esc) 24%,transparent);}
.metric.needs .m-l{color:var(--st-esc-fg);opacity:0.8;}
.changelog-link{flex:0 0 auto;}
.head-actions{display:flex;gap:var(--s2);flex:0 0 auto;}
.icon-btn{
  flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;
  width:32px;height:32px;padding:0;
  border:1px solid var(--border);background:var(--surface);color:var(--text-muted);
  box-shadow:var(--sh-sm);transition:color .15s, border-color .15s, background .15s;
}
.icon-btn:hover{color:var(--text-strong);border-color:var(--border-strong);}
.icon-btn svg{width:15px;height:15px;}
.theme-toggle .ic-moon{display:none;}
:root[data-theme="dark"] .theme-toggle .ic-sun{display:none;}
:root[data-theme="dark"] .theme-toggle .ic-moon{display:inline;}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]) .theme-toggle .ic-sun{display:none;}
  :root:not([data-theme="light"]) .theme-toggle .ic-moon{display:inline;}
}

/* queue ribbon */
main{padding:var(--s8) 0 var(--s16);}
.ribbon{
  margin-bottom:var(--s8);padding:var(--s4) var(--s5);
  background:var(--surface);border:1px solid var(--border);box-shadow:var(--sh-sm);
}
.ribbon-bar{display:flex;height:6px;gap:2px;margin-bottom:var(--s4);}
.ribbon-bar span{display:block;background:var(--d);transition:flex .3s;}
.ribbon-legend{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s2) var(--s6);}
.leg{display:inline-flex;align-items:center;gap:7px;font-size:var(--fs-sm);color:var(--text);}
.leg-dot{width:8px;height:8px;flex:0 0 auto;background:var(--d);}
.leg-name{color:var(--text);font-weight:500;}
.leg-n{font-family:var(--font-mono);font-size:var(--fs-xs);font-weight:600;color:var(--text-faint);font-variant-numeric:tabular-nums;}
.leg-z .leg-name,.leg-z .leg-n{color:var(--text-faint);}
.leg-z .leg-dot{opacity:0.4;}

/* section headers */
.sec{margin-bottom:var(--s10);scroll-margin-top:80px;}
.sec-head{display:flex;align-items:center;gap:var(--s3);margin-bottom:var(--s5);flex-wrap:wrap;}
.sec-title{font-size:var(--fs-lg);font-weight:600;color:var(--text-strong);letter-spacing:-0.02em;display:flex;align-items:center;gap:var(--s3);}
.sec-count{font-family:var(--font-mono);font-size:var(--fs-sm);font-weight:500;color:var(--text-muted);background:var(--surface-2);border:1px solid var(--border);padding:2px 8px;border-radius:var(--r-pill);}
.sec-sub{font-size:var(--fs-sm);color:var(--text-faint);}

/* needs-you zone */
.needs-zone{
  position:relative;padding:var(--s2);border-radius:var(--r-xl);
  background:linear-gradient(180deg, color-mix(in oklch, var(--st-esc-bg) 60%, var(--surface)), var(--surface));
  border:1px solid color-mix(in oklch, var(--st-esc) 32%, var(--border));
  box-shadow:var(--sh-md);
}
.needs-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:var(--s2);}
@media (max-width:520px){ .needs-grid{grid-template-columns:1fr;} }

.allclear{
  display:flex;align-items:center;gap:var(--s5);
  padding:var(--s6) var(--s8);border-radius:var(--r-lg);
  background:var(--surface);border:1px solid var(--border);box-shadow:var(--sh-sm);
}
.allclear-ic{
  width:48px;height:48px;flex:0 0 auto;display:grid;place-items:center;
  background:var(--st-don-bg);color:var(--st-don-fg);
}
.allclear-ic svg{width:24px;height:24px;}
.allclear h3{font-size:var(--fs-md);font-weight:600;color:var(--text-strong);}
.allclear p{font-size:var(--fs-base);color:var(--text-muted);margin-top:2px;}

/* filters */
.filters{
  display:flex;align-items:center;justify-content:space-between;gap:var(--s5);
  flex-wrap:wrap;margin-bottom:var(--s6);
}
.chips{display:flex;gap:var(--s2);flex-wrap:wrap;}
.chip{
  display:inline-flex;align-items:center;gap:var(--s2);height:32px;padding:0 var(--s4);
  border-radius:var(--r-pill);border:1px solid var(--border);background:var(--surface);
  color:var(--text-muted);font-size:var(--fs-sm);font-weight:500;
  transition:color .15s, border-color .15s, background .15s, box-shadow .15s;
}
.chip:hover{color:var(--text-strong);border-color:var(--border-strong);}
.chip[aria-pressed="true"]{background:var(--text-strong);color:var(--surface);border-color:var(--text-strong);box-shadow:var(--sh-sm);}
.chip .cc{font-family:var(--font-mono);font-size:var(--fs-xs);opacity:0.6;}
.chip[aria-pressed="true"] .cc{opacity:0.85;}

.segmented{display:inline-flex;padding:3px;gap:2px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--r-pill);}
.seg{
  height:28px;padding:0 var(--s4);border-radius:var(--r-pill);
  font-size:var(--fs-sm);font-weight:500;color:var(--text-muted);
  transition:color .15s, background .15s, box-shadow .15s;white-space:nowrap;
}
.seg:hover{color:var(--text-strong);}
.seg[aria-pressed="true"]{background:var(--surface);color:var(--text-strong);box-shadow:var(--sh-sm);font-weight:600;}

/* project card */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:var(--s5);}
@media (max-width:380px){ .grid{grid-template-columns:1fr;} }

.card{
  position:relative;display:flex;flex-direction:column;
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r-lg);box-shadow:var(--sh-sm);
  padding:var(--s5) var(--s5) var(--s4) calc(var(--s5) + 4px);
  overflow:hidden;transition:border-color .18s, box-shadow .18s, transform .18s;
}
.card::before{
  content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--s);
}
.card:hover{border-color:var(--border-strong);box-shadow:var(--sh-md);transform:translateY(-2px);}

.card.hero{
  padding:var(--s6) var(--s6) var(--s5) calc(var(--s6) + 5px);
  border-color:color-mix(in oklch, var(--st-esc) 38%, var(--border));
  box-shadow:var(--sh-md);
}
.card.hero::before{width:5px;}

.card-top{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--s3);}
.card-id{min-width:0;}
.card-name{font-size:var(--fs-lg);font-weight:600;color:var(--text-strong);letter-spacing:-0.02em;line-height:var(--lh-tight);display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap;}
.hero .card-name{font-size:var(--fs-xl);}
.card-desc{font-size:var(--fs-base);color:var(--text-muted);margin-top:var(--s2);line-height:var(--lh-snug);text-wrap:pretty;}

.pill{
  display:inline-flex;align-items:center;gap:6px;height:24px;padding:0 10px 0 8px;
  border-radius:var(--r-pill);font-size:var(--fs-xs);font-weight:600;letter-spacing:0.01em;
  background:var(--s-bg);color:var(--s-fg);white-space:nowrap;flex:0 0 auto;
  border:1px solid color-mix(in oklch, var(--s) 22%, transparent);
}
.pill .dot{width:7px;height:7px;background:var(--s);}
.pill.running .dot{animation:pulse 1.6s ease-in-out infinite;}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 color-mix(in oklch,var(--s) 60%,transparent);}50%{box-shadow:0 0 0 5px color-mix(in oklch,var(--s) 0%,transparent);}}

.badges{display:flex;gap:6px;flex-wrap:wrap;margin-top:var(--s3);}
.badge{
  display:inline-flex;align-items:center;gap:5px;height:22px;padding:0 9px;
  border-radius:var(--r-sm);font-size:var(--fs-xs);font-weight:500;
  font-family:var(--font-mono);letter-spacing:0.01em;
  background:var(--surface-2);color:var(--text-muted);border:1px solid var(--border);white-space:nowrap;
}
.badge svg{width:11px;height:11px;}
.badge.auto{background:var(--st-rev-bg);color:var(--st-rev-fg);border-color:color-mix(in oklch,var(--st-rev) 22%,transparent);}
.badge.blocked{background:var(--st-esc-bg);color:var(--st-esc-fg);border-color:color-mix(in oklch,var(--st-esc) 24%,transparent);}
.badge.stale{background:var(--st-stale-bg);color:var(--st-stale-fg);border-color:color-mix(in oklch,var(--st-stale) 26%,transparent);}
.badge.cat,.badge.energy{text-transform:capitalize;}
.badge.fresh-stale{background:var(--st-stale-bg);color:var(--st-stale-fg);border-color:color-mix(in oklch,var(--st-stale) 26%,transparent);}

.divider{height:1px;background:var(--border);margin:var(--s4) 0;}

.blocked-line{
  display:flex;align-items:flex-start;gap:var(--s2);margin-top:var(--s3);
  padding:var(--s3);border-radius:var(--r-md);
  background:var(--st-esc-bg);border:1px solid color-mix(in oklch,var(--st-esc) 20%,transparent);
  font-size:var(--fs-base);color:var(--st-esc-fg);line-height:var(--lh-snug);
}
.blocked-line svg{width:15px;height:15px;flex:0 0 auto;margin-top:2px;}
.blocked-line b{font-weight:600;}

.fired-line{
  display:flex;align-items:center;gap:6px;margin-top:var(--s3);
  font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--text-faint);
}
.fired-line svg{width:12px;height:12px;flex:0 0 auto;color:var(--st-rev-fg);}

.log{display:flex;flex-direction:column;gap:var(--s2);margin-top:var(--s2);}
.log-row{display:flex;gap:var(--s3);font-size:var(--fs-base);line-height:var(--lh-snug);}
.log-date{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--text-faint);flex:0 0 auto;width:42px;padding-top:1px;letter-spacing:-0.02em;}
.log-text{color:var(--text);text-wrap:pretty;}
.log-cap{font-size:var(--fs-xs);font-family:var(--font-mono);text-transform:uppercase;letter-spacing:0.05em;color:var(--text-faint);margin-top:var(--s4);margin-bottom:var(--s1);}

.expander{margin-top:var(--s2);}
.exp-body{display:none;}
.card[data-open="true"] .exp-body{display:block;animation:fade .2s ease;}
@keyframes fade{from{opacity:0;transform:translateY(-3px);}to{opacity:1;transform:none;}}
.prompt-cap{display:flex;align-items:center;justify-content:space-between;gap:var(--s3);margin-top:var(--s4);margin-bottom:var(--s2);}
.prompt{
  font-family:var(--font-mono);font-size:var(--fs-sm);line-height:1.6;
  color:var(--text);background:var(--surface-2);border:1px solid var(--border);
  border-radius:var(--r-md);padding:var(--s4);white-space:pre-wrap;text-wrap:pretty;
}

.card-foot{display:flex;align-items:center;gap:var(--s3);margin-top:auto;padding-top:var(--s5);}
.exp-toggle{
  display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 var(--s3);
  border-radius:var(--r-md);color:var(--text-muted);font-size:var(--fs-sm);font-weight:500;
  border:1px solid transparent;transition:color .15s, background .15s, border-color .15s;
}
.exp-toggle:hover{color:var(--text-strong);background:var(--surface-2);}
.exp-toggle svg{width:14px;height:14px;transition:transform .2s;}
.card[data-open="true"] .exp-toggle svg{transform:rotate(180deg);}
.foot-spacer{flex:1 1 auto;}

.btn-copy{
  display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 var(--s4);
  border-radius:var(--r-md);font-size:var(--fs-sm);font-weight:600;
  background:var(--text-strong);color:var(--surface);
  box-shadow:var(--sh-sm);transition:transform .12s, filter .15s, background .2s;
}
.btn-copy svg{width:14px;height:14px;}
.btn-copy:hover{filter:brightness(1.08);}
:root[data-theme="dark"] .btn-copy:hover, :root:not([data-theme="light"]) .btn-copy:hover{filter:brightness(0.92);}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]) .btn-copy:hover{filter:brightness(0.92);}}
.btn-copy:active{transform:scale(0.97);}
.btn-copy.copied{background:var(--st-don);color:#fff;}
.btn-copy .ic-check{display:none;}
.btn-copy.copied .ic-copy{display:none;}
.btn-copy.copied .ic-check{display:inline;}

.btn-copy-ghost{
  display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 var(--s3);
  border-radius:var(--r-sm);font-size:var(--fs-xs);font-weight:600;font-family:var(--font-mono);
  border:1px solid var(--border);background:var(--surface);color:var(--text-muted);
  transition:color .15s, border-color .15s;
}
.btn-copy-ghost svg{width:12px;height:12px;}
.btn-copy-ghost:hover{color:var(--text-strong);border-color:var(--border-strong);}
.btn-copy-ghost.copied{color:var(--st-don-fg);border-color:color-mix(in oklch,var(--st-don) 30%,transparent);background:var(--st-don-bg);}
.btn-copy-ghost .ic-check{display:none;}
.btn-copy-ghost.copied .ic-copy{display:none;}
.btn-copy-ghost.copied .ic-check{display:inline;}

.empty{
  grid-column:1/-1;text-align:center;padding:var(--s12) var(--s6);
  color:var(--text-faint);font-size:var(--fs-base);
  border:1px dashed var(--border-strong);border-radius:var(--r-lg);
}

.card.is-hidden{display:none;}

/* changelog timeline */
.timeline{position:relative;}
.day{position:relative;padding-left:var(--s8);margin-bottom:var(--s6);}
.day::before{
  content:"";position:absolute;left:7px;top:18px;bottom:-24px;width:2px;background:var(--border);
}
.day:last-child::before{display:none;}
.day-node{
  position:absolute;left:0;top:4px;width:14px;height:14px;
  background:var(--surface);border:2px solid var(--border-strong);
}
.day:first-child .day-node{border-color:var(--st-run);background:var(--st-run-bg);}
.day-head{display:flex;align-items:baseline;gap:var(--s3);margin-bottom:var(--s3);}
.day-label{font-size:var(--fs-md);font-weight:600;color:var(--text-strong);letter-spacing:-0.01em;}
.day-date{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--text-faint);letter-spacing:0.02em;}
.entry{
  display:grid;grid-template-columns:auto 116px 1fr;align-items:start;
  column-gap:var(--s3);padding:var(--s2) var(--s3);
  border-radius:var(--r-md);transition:background .15s;
}
.entry:hover{background:var(--surface-2);}
.entry + .entry{margin-top:2px;}
.entry-dot{width:8px;height:8px;background:var(--s);flex:0 0 auto;margin-top:6px;}
.entry-proj{font-size:var(--fs-xs);font-weight:600;font-family:var(--font-mono);color:var(--s-fg);background:var(--s-bg);padding:2px 7px;letter-spacing:0.01em;white-space:nowrap;justify-self:start;}
.entry-text{font-size:var(--fs-base);color:var(--text);line-height:var(--lh-snug);}

footer.foot{
  border-top:1px solid var(--border);margin-top:var(--s12);padding:var(--s6) 0;
  font-size:var(--fs-sm);color:var(--text-faint);display:flex;gap:var(--s3);align-items:center;flex-wrap:wrap;
}
footer.foot .mono{color:var(--text-muted);}

@media (max-width:760px){
  .metrics{display:none;}
}
@media (max-width:680px){
  .wrap{padding:0 var(--s4);}
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

  /* ---- theme toggle (defaults to prefers-color-scheme) ---- */
  var KEY = "legwork-theme";
  var toggle = document.getElementById("themeToggle");
  function systemDark(){ return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches; }
  function effective(){ var a = root.getAttribute("data-theme"); return a ? a : (systemDark() ? "dark" : "light"); }
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
  if(window.matchMedia){
    try{ window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", syncLabel); }catch(e){}
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
