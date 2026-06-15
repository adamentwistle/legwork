#!/usr/bin/env python3
"""Build dashboard/index.html from projects/*.md.

Zero dependencies. Run from anywhere:

    python3 scripts/build_dashboard.py

Reads simple frontmatter (key: value lines between --- markers), the first
fenced block under '## Next prompt', and bullet lines under '## Log'.
The HTML is a build artifact: every run replaces it wholesale. All styling
lives in the TEMPLATE below; project data never lives in this file.
"""

import html
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
OUT_FILE = ROOT / "dashboard" / "index.html"

STATUSES = ["escalated", "queued", "running", "review", "done", "icebox"]
ORDER = {s: i for i, s in enumerate(STATUSES)}
STATUS_LABEL = {
    "escalated": "needs you", "queued": "ready", "running": "running",
    "review": "in review", "done": "done", "icebox": "icebox",
}
STALE_DAYS = 14

PROMPT_RE = re.compile(r"##\s*Next prompt.*?```[a-zA-Z]*\n(.*?)```", re.S)
LOG_RE = re.compile(r"##\s*Log\s*\n(.*?)(?:\n##|\Z)", re.S)


def parse_frontmatter(text):
    meta = {}
    parts = text.split("---")
    if len(parts) < 3:
        return meta
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta


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

    def to_date(value):
        try:
            y, m, d = (int(x) for x in value[:10].split("-"))
            return date(y, m, d)
        except (ValueError, AttributeError):
            return None

    # Freshness is the newest of the frontmatter date and the latest log
    # entry, so a forgotten `updated` bump cannot make a project look stale.
    candidates = [to_date(meta.get("updated", ""))]
    if log_lines:
        candidates.append(to_date(log_lines[0]))
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


def quiet_pill(p):
    d = p["days_quiet"]
    if d is None:
        return ""
    label = "today" if d == 0 else f"{d}d quiet"
    cls = "pill stale" if d > STALE_DAYS else "pill"
    return f'<span class="{cls}">{label}</span>'


def pills(p):
    s = p["status"]
    out = [f'<span class="pill st-{s}"><i class="dot"></i>{STATUS_LABEL[s]}</span>']
    if p["autonomy"] == "loop":
        out.append('<span class="pill auto"><i class="dot"></i>auto</span>')
    if p["blocked_on"]:
        out.append('<span class="pill blocked"><i class="dot"></i>blocked</span>')
    out.append(f'<span class="pill">{html.escape(p["category"])}</span>')
    if p["energy"]:
        out.append(f'<span class="pill">{html.escape(p["energy"])}</span>')
    out.append(quiet_pill(p))
    return "".join(out)


def card(p, copy_label="Copy prompt"):
    last = html.escape(p["log"][0]) if p["log"] else ""
    last_block = f'<p class="last">Last: {last}</p>' if last else ""
    if p["blocked_on"]:
        last_block = (f'<p class="blockedon">Blocked on: '
                      f'{html.escape(p["blocked_on"])}</p>') + last_block
    footer = ""
    if p["prompt"]:
        items = "".join(f"<li>{html.escape(x)}</li>" for x in p["log"])
        log_ul = f"<ul>{items}</ul>" if items else ""
        footer = f"""
      <div class="foot">
        <button class="copy" type="button">{copy_label}</button>
        <details class="more">
          <summary>View prompt</summary>
          <pre>{html.escape(p["prompt"])}</pre>
          {log_ul}
        </details>
      </div>"""
    actionable = "1" if p["status"] in ("escalated", "queued", "running", "review") else "0"
    return f"""
    <article class="card" data-category="{html.escape(p["category"])}" data-status="{p["status"]}" data-actionable="{actionable}">
      <header>
        <h3>{html.escape(p["name"])}</h3>
        <div class="pills">{pills(p)}</div>
      </header>
      <p class="desc">{html.escape(p["description"])}</p>
      {last_block}{footer}
    </article>"""


DATED_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):\s*(.+)$", re.S)


def changelog_html(projects):
    """Every dated log line across every project, newest day first. This is
    the progress record: one place to see what actually moved."""
    by_day = {}
    for p in projects:
        for line in p["log_all"]:
            m = DATED_LINE_RE.match(line)
            if not m:
                continue
            by_day.setdefault(m.group(1), []).append((p["name"], m.group(2)))
    if not by_day:
        return '<div class="allclear"><i class="dot ok"></i>No log entries yet</div>'
    sections = []
    for day in sorted(by_day, reverse=True):
        try:
            y, mo, d = (int(x) for x in day.split("-"))
            heading = date(y, mo, d).strftime("%a %d %b %Y")
        except ValueError:
            heading = day
        items = "".join(
            f'<li><span class="proj">{html.escape(name)}</span> {html.escape(text)}</li>'
            for name, text in sorted(by_day[day])
        )
        sections.append(f'<section class="day"><h3>{heading}</h3><ul>{items}</ul></section>')
    return "".join(sections)


def sort_key(p):
    quiet = p["days_quiet"] if p["days_quiet"] is not None else 0
    # Stalest first for queued (the ones most at risk of dying), freshest
    # first for everything else.
    tiebreak = -quiet if p["status"] == "queued" else quiet
    return (ORDER[p["status"]], tiebreak, p["name"].lower())


def build(projects):
    escalated = [p for p in projects if p["status"] == "escalated"]
    rest = sorted([p for p in projects if p["status"] != "escalated"], key=sort_key)

    active = sum(1 for p in projects if p["status"] in ("escalated", "queued", "running", "review"))
    categories = sorted({p["category"] for p in projects})

    if escalated:
        needs_me = "".join(card(p, copy_label="Copy brief") for p in escalated)
    else:
        needs_me = '<div class="allclear"><i class="dot ok"></i>Nothing needs you</div>'

    cat_chips = "".join(
        f'<button class="chip" data-filter="category" data-value="{html.escape(c)}">{html.escape(c)}</button>'
        for c in categories
    )

    grid = "".join(card(p) for p in rest)

    today = date.today().strftime("%a %d %b %Y")
    return TEMPLATE.format(
        today=today,
        active=active,
        total=len(projects),
        escalated=len(escalated),
        needs_me=needs_me,
        cat_chips=cat_chips,
        grid=grid,
        changelog=changelog_html(projects),
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120">
<title>Legwork</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #fafafa; --card: #ffffff; --inset: #fafafa;
    --border: #eaeaea; --fg: #000000; --muted: #666666; --faint: #999999;
    --blue: #0070f3; --amber: #f5a623; --green: #29a383; --violet: #7928ca;
    --shadow: 0 1px 2px rgba(0,0,0,.04);
    --shadow-hover: 0 4px 12px rgba(0,0,0,.08);
    --btn-bg: #000; --btn-fg: #fff;
    --sans: "Geist", -apple-system, system-ui, "Segoe UI", sans-serif;
    --mono: "Geist Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #000000; --card: #0a0a0a; --inset: #000000;
      --border: #333333; --fg: #ededed; --muted: #888888; --faint: #666666;
      --shadow: none; --shadow-hover: 0 0 0 1px #444;
      --btn-bg: #ededed; --btn-fg: #000;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0 24px 96px; background: var(--bg); color: var(--fg);
    font: 14px/1.6 var(--sans); -webkit-font-smoothing: antialiased;
  }}
  main {{ max-width: 1080px; margin: 0 auto; }}
  .masthead {{
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 8px; padding: 22px 0 18px;
    border-bottom: 1px solid var(--border); margin-bottom: 8px;
  }}
  .masthead h1 {{ margin: 0; font-size: 16px; font-weight: 600; letter-spacing: -0.02em; }}
  .meta {{ color: var(--muted); font-size: 13px; }}
  h2 {{
    display: flex; align-items: center; gap: 8px;
    font-size: 13px; font-weight: 500; color: var(--muted);
    margin: 40px 0 14px;
  }}
  .filters {{
    display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
    margin: 36px 0 16px;
  }}
  .chip {{
    cursor: pointer; font: 500 12px var(--sans); color: var(--muted);
    background: var(--card); border: 1px solid var(--border);
    border-radius: 999px; padding: 5px 14px; transition: all .15s ease;
  }}
  .chip:hover {{ border-color: var(--fg); color: var(--fg); }}
  .chip.on {{ background: var(--btn-bg); color: var(--btn-fg); border-color: var(--btn-bg); }}
  .sep {{ width: 1px; height: 18px; background: var(--border); }}
  .grid {{
    display: grid; gap: 12px;
    grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
  }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    padding: 18px; box-shadow: var(--shadow); transition: box-shadow .15s ease;
    display: flex; flex-direction: column; gap: 8px;
  }}
  .card:hover {{ box-shadow: var(--shadow-hover); }}
  #needsme .card {{ margin-bottom: 12px; }}
  .card header {{
    display: flex; justify-content: space-between; gap: 10px;
    flex-wrap: wrap; align-items: center;
  }}
  .card h3 {{ margin: 0; font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }}
  .desc {{ margin: 0; color: var(--muted); }}
  .last {{
    margin: 0; color: var(--faint); font-size: 12.5px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .pills {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .pill {{
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 12px; color: var(--muted); background: var(--card);
    border: 1px solid var(--border); border-radius: 999px;
    padding: 2px 10px; white-space: nowrap;
  }}
  .dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--faint); }}
  .pill.stale {{ color: var(--amber); border-color: var(--amber); }}
  .pill.auto {{ color: var(--blue); border-color: var(--blue); }}
  .pill.auto .dot {{ background: var(--blue); }}
  .pill.blocked {{ color: var(--amber); border-color: var(--amber); }}
  .pill.blocked .dot {{ background: var(--amber); }}
  .blockedon {{ margin: 0; color: var(--amber); font-size: 12.5px; }}
  .st-escalated {{ color: var(--amber); border-color: var(--amber); }}
  .st-escalated .dot {{ background: var(--amber); }}
  .st-running {{ color: var(--blue); border-color: var(--blue); }}
  .st-running .dot {{ background: var(--blue); }}
  .st-review {{ color: var(--violet); border-color: var(--violet); }}
  .st-review .dot {{ background: var(--violet); }}
  .st-done {{ color: var(--green); border-color: var(--green); }}
  .st-done .dot {{ background: var(--green); }}
  .st-queued .dot {{ background: var(--fg); }}
  .st-icebox {{ color: var(--faint); }}
  .foot {{ display: flex; gap: 10px; align-items: baseline; margin-top: 4px; }}
  .copy {{
    cursor: pointer; flex-shrink: 0;
    font: 500 12px var(--sans); color: var(--btn-fg); background: var(--btn-bg);
    border: 1px solid var(--btn-bg); border-radius: 6px; padding: 6px 14px;
    transition: all .15s ease;
  }}
  .copy:hover {{ background: var(--card); color: var(--fg); border-color: var(--fg); }}
  .copy.copied {{ background: var(--green); border-color: var(--green); color: #fff; }}
  .more {{ flex: 1; min-width: 0; }}
  .more summary {{ cursor: pointer; font-size: 12px; color: var(--faint); }}
  .more pre {{
    margin: 10px 0 0; padding: 12px; background: var(--inset);
    border: 1px solid var(--border); border-radius: 6px;
    font: 12.5px/1.65 var(--mono); white-space: pre-wrap;
    word-break: break-word; color: var(--fg);
  }}
  .more ul {{ margin: 10px 0 0; padding-left: 18px; color: var(--muted); font-size: 13px; }}
  .allclear {{
    display: flex; align-items: center; justify-content: center; gap: 8px;
    border: 1px dashed var(--border); border-radius: 8px; padding: 26px;
    color: var(--muted); background: var(--card); font-size: 13px;
  }}
  .allclear .dot.ok {{ background: var(--green); }}
  .empty {{
    display: none; border: 1px dashed var(--border); border-radius: 8px;
    padding: 26px; text-align: center; color: var(--faint); font-size: 13px;
  }}
  .meta a {{ color: inherit; }}
  .changelog .day h3 {{
    font-size: 12px; font-weight: 500; color: var(--faint); margin: 18px 0 6px;
  }}
  .changelog ul {{ margin: 0; padding-left: 18px; color: var(--muted); font-size: 13px; }}
  .changelog li {{ margin: 3px 0; }}
  .changelog .proj {{ color: var(--fg); font-weight: 500; }}
  @media (max-width: 560px) {{ body {{ padding: 0 14px 64px; }} .card {{ padding: 16px; }} }}
</style>
</head>
<body>
<main>
  <div class="masthead">
    <h1>Legwork</h1>
    <span class="meta">{today} &nbsp;&middot;&nbsp; {total} projects &nbsp;&middot;&nbsp; {active} active &nbsp;&middot;&nbsp; {escalated} need you &nbsp;&middot;&nbsp; <a href="#changelog">changelog</a></span>
  </div>

  <h2>Needs me</h2>
  <div id="needsme">{needs_me}</div>

  <div class="filters">
    <button class="chip on" data-filter="category" data-value="all">All</button>
    {cat_chips}
    <span class="sep"></span>
    <button class="chip on" data-filter="state" data-value="all">Everything</button>
    <button class="chip" data-filter="state" data-value="actionable">Actionable</button>
    <button class="chip" data-filter="state" data-value="done">Done</button>
    <button class="chip" data-filter="state" data-value="icebox">Icebox</button>
  </div>

  <div class="grid" id="grid">{grid}</div>
  <div class="empty" id="empty">No projects match these filters</div>

  <h2 id="changelog">Changelog</h2>
  <div class="changelog">{changelog}</div>
</main>
<script>
  document.querySelectorAll(".copy").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      var pre = btn.closest(".card").querySelector("pre");
      if (!pre) return;
      var label = btn.textContent;
      navigator.clipboard.writeText(pre.textContent).then(function () {{
        btn.textContent = "Copied";
        btn.classList.add("copied");
        setTimeout(function () {{
          btn.textContent = label;
          btn.classList.remove("copied");
        }}, 1600);
      }});
    }});
  }});

  var sel = {{ category: "all", state: "all" }};
  function applyFilters() {{
    var visible = 0;
    document.querySelectorAll("#grid .card").forEach(function (c) {{
      var okCat = sel.category === "all" || c.dataset.category === sel.category;
      var okState =
        sel.state === "all" ||
        (sel.state === "actionable" && c.dataset.actionable === "1") ||
        c.dataset.status === sel.state;
      var show = okCat && okState;
      c.style.display = show ? "" : "none";
      if (show) visible++;
    }});
    document.getElementById("empty").style.display = visible ? "none" : "block";
  }}
  document.querySelectorAll(".chip").forEach(function (chip) {{
    chip.addEventListener("click", function () {{
      var group = chip.dataset.filter;
      sel[group] = chip.dataset.value;
      document.querySelectorAll('.chip[data-filter="' + group + '"]').forEach(function (c) {{
        c.classList.toggle("on", c === chip);
      }});
      applyFilters();
    }});
  }});
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
