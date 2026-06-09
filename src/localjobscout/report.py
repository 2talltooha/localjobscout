"""Generate a static HTML report of all scored jobs, sorted by match score.

The report is a single self-contained file (inline CSS + vanilla JS) opened
directly in the user's browser. No web server, no extra runtime dependencies.

Columns: rank, score, source, title (linked to the job URL), company,
location, posted date, notified flag, threshold flag.

Client-side features:
- Click any column header to re-sort (asc / desc toggle).
- Filter chips per source toggle visibility.
- Threshold slider re-flags rows live.
- Text search across title + company.
"""
from __future__ import annotations

import html
import logging
import webbrowser
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from localjobscout.db import Job

logger = logging.getLogger(__name__)

_SOURCE_COLORS = {
    "jobbank": "#0a66c2",
    "adzuna": "#1abc9c",
    "remoteok": "#9b59b6",
    "linkedin": "#0077b5",
    "indeed": "#003a9b",
    "uoguelph": "#c8102e",
    "uwaterloo": "#ffd54f",
    "conestoga": "#f57c00",
    "laurier": "#5a2d82",
}

_STATUS_COLORS = {
    "interested": "#3b82f6",
    "applied": "#10b981",
    "interviewed": "#8b5cf6",
    "offered": "#22c55e",
    "rejected": "#ef4444",
    "hidden": "#6b7280",
}


def _fmt_score(score: float | None) -> str:
    return f"{score:.3f}" if score is not None else "—"


def _fmt_date(s: str | None) -> str:
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return s[:10] if isinstance(s, str) else ""


def _status_badge_html(status: str | None) -> str:
    if not status:
        return ""
    color = _STATUS_COLORS.get(status, "#666")
    return (
        f'<span class="badge status" style="background:{color}">'
        f"{html.escape(status)}</span>"
    )


def _action_buttons_html(job: Job) -> str:
    short = job.id[:8]
    return (
        f'<div class="actions">'
        f'<a class="btn open" href="{html.escape(job.url)}" '
        f'target="_blank" rel="noopener" title="Open posting">Open</a>'
        f'<button class="btn copy-cmd" '
        f'data-cmd="python -m localjobscout --mark interested {short}" '
        f'title="Copy interested command">★ interested</button>'
        f'<button class="btn copy-cmd" '
        f'data-cmd="python -m localjobscout --mark applied {short}" '
        f'title="Copy applied command">✓ applied</button>'
        f'<button class="btn copy-cmd" '
        f'data-cmd="python -m localjobscout --cover {short}" '
        f'title="Copy cover-letter command">✉ cover</button>'
        f"</div>"
    )


def _row_html(rank: int, job: Job, threshold: float) -> str:
    score = job.score if job.score is not None else 0.0
    above = score >= threshold
    color = _SOURCE_COLORS.get(job.source, "#666")
    notified_badge = (
        '<span class="badge notified">notified</span>'
        if job.notified
        else ""
    )
    above_badge = (
        '<span class="badge above">✓ above</span>'
        if above
        else '<span class="badge below">below</span>'
    )
    title_link = (
        f'<a href="{html.escape(job.url)}" target="_blank" rel="noopener">'
        f"{html.escape(job.title)}</a>"
    )
    status_badge = _status_badge_html(job.application_status)
    actions = _action_buttons_html(job)
    return (
        f'<tr class="job-row" '
        f'data-source="{html.escape(job.source)}" '
        f'data-score="{score:.6f}" '
        f'data-notified="{int(job.notified)}" '
        f'data-status="{html.escape(job.application_status or "")}" '
        f'data-search="{html.escape((job.title + " " + job.company).lower())}">'
        f"<td class='rank'>{rank}</td>"
        f"<td class='score'>{_fmt_score(job.score)}</td>"
        f"<td class='flag'>{above_badge}{notified_badge}{status_badge}</td>"
        f"<td><span class='source-chip' "
        f"style='background:{color}'>{html.escape(job.source)}</span></td>"
        f"<td class='title'>{title_link}</td>"
        f"<td>{html.escape(job.company)}</td>"
        f"<td>{html.escape(job.location)}</td>"
        f"<td class='posted'>{_fmt_date(job.posted_at or job.first_seen)}</td>"
        f"<td class='actions-cell'>{actions}</td>"
        f"</tr>"
    )


def _source_chip_html(source: str, count: int) -> str:
    color = _SOURCE_COLORS.get(source, "#666")
    return (
        f'<label class="source-toggle">'
        f'<input type="checkbox" data-source-filter="{html.escape(source)}" '
        f'checked>'
        f'<span class="source-chip" style="background:{color}">'
        f"{html.escape(source)} ({count})</span>"
        f"</label>"
    )


def render_html(
    scored: list[tuple[Job, float]],
    threshold: float,
    *,
    generated_at: datetime | None = None,
) -> str:
    """Render the scored jobs list into a standalone HTML page.

    Jobs are sorted by score descending. `scored` is the output of
    `Matcher.score_jobs(...)` — a list of `(Job, float)` tuples where the
    float is the freshly computed score (which may differ from `Job.score`
    stored in the DB).
    """
    # Stamp the freshly computed score onto each job so the HTML reflects
    # the latest match (DB score may be stale relative to the current resume).
    jobs: list[Job] = []
    for job, fresh_score in scored:
        job.score = fresh_score
        jobs.append(job)
    jobs.sort(key=lambda j: j.score or 0.0, reverse=True)

    generated_at = generated_at or datetime.now(UTC)
    source_counts = Counter(j.source for j in jobs)
    above_count = sum(1 for j in jobs if (j.score or 0.0) >= threshold)
    notified_count = sum(1 for j in jobs if j.notified)
    tracked_count = sum(1 for j in jobs if j.application_status)
    total_count = len(jobs)

    rows = "\n".join(
        _row_html(rank, job, threshold)
        for rank, job in enumerate(jobs, start=1)
    )
    source_chips = "\n".join(
        _source_chip_html(src, source_counts[src])
        for src in sorted(source_counts)
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LocalJobScout — Job Report</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 Helvetica, Arial, sans-serif;
    margin: 0; padding: 0;
    background: #f5f6f8; color: #1a1a1a;
  }}
  header {{
    background: #1f2937; color: #fff; padding: 18px 28px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  }}
  header h1 {{ margin: 0; font-size: 22px; }}
  header .meta {{ color: #9ca3af; font-size: 13px; margin-top: 4px; }}
  .summary {{
    background: #fff; padding: 12px 28px; display: flex; gap: 24px;
    border-bottom: 1px solid #e5e7eb; flex-wrap: wrap;
  }}
  .summary .stat {{ font-size: 14px; }}
  .summary .stat strong {{
    display: block; font-size: 22px; color: #111;
  }}
  .controls {{
    padding: 14px 28px; background: #fff;
    border-bottom: 1px solid #e5e7eb;
    display: flex; gap: 20px; align-items: center; flex-wrap: wrap;
  }}
  .controls input[type=search] {{
    padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px;
    min-width: 240px; font-size: 14px;
  }}
  .controls label {{ font-size: 13px; }}
  .source-toggle {{ cursor: pointer; user-select: none; }}
  .source-toggle input {{ display: none; }}
  .source-toggle input:not(:checked) + .source-chip {{
    opacity: 0.35; text-decoration: line-through;
  }}
  .source-chip {{
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    color: #fff; font-size: 12px; font-weight: 600;
  }}
  table {{
    width: 100%; border-collapse: collapse; background: #fff;
    font-size: 14px;
  }}
  th, td {{
    padding: 8px 12px; text-align: left;
    border-bottom: 1px solid #eef0f3; vertical-align: top;
  }}
  th {{
    background: #f8fafc; color: #4b5563; font-weight: 600;
    cursor: pointer; user-select: none; position: sticky; top: 0;
    border-bottom: 2px solid #e2e8f0;
  }}
  th:hover {{ background: #eef2f7; }}
  th[data-sorted=asc]::after  {{ content: " ▲"; color: #6b7280; }}
  th[data-sorted=desc]::after {{ content: " ▼"; color: #6b7280; }}
  td.rank  {{ color: #9ca3af; width: 48px; }}
  td.score {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
  td.title a {{ color: #0a66c2; text-decoration: none; font-weight: 500; }}
  td.title a:hover {{ text-decoration: underline; }}
  td.posted {{ color: #6b7280; font-size: 12px; white-space: nowrap; }}
  .badge {{
    display: inline-block; padding: 2px 7px; border-radius: 4px;
    font-size: 11px; font-weight: 600; margin-right: 4px;
  }}
  .badge.above {{ background: #d1fae5; color: #065f46; }}
  .badge.below {{ background: #f1f5f9; color: #94a3b8; }}
  .badge.notified {{ background: #fde68a; color: #92400e; }}
  .badge.status {{ color: #fff; }}
  tr.hidden {{ display: none; }}
  tr.job-row.above-threshold {{ background: #f0fdf4; }}
  td.actions-cell {{ white-space: nowrap; }}
  .actions {{ display: flex; gap: 4px; flex-wrap: wrap; }}
  .btn {{
    display: inline-block; padding: 3px 8px; font-size: 11px;
    border: 1px solid #cbd5e1; background: #f8fafc; color: #1f2937;
    border-radius: 4px; cursor: pointer; text-decoration: none;
    font-family: inherit;
  }}
  .btn:hover {{ background: #e2e8f0; }}
  .btn.open {{ background: #0a66c2; color: #fff; border-color: #0a66c2; }}
  .btn.open:hover {{ background: #084d96; }}
  .btn.copied {{ background: #10b981; color: #fff; border-color: #10b981; }}
  .toast {{
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    padding: 10px 18px; background: #1f2937; color: #fff;
    border-radius: 6px; font-size: 13px; opacity: 0;
    transition: opacity 0.2s ease; pointer-events: none;
    box-shadow: 0 4px 12px rgba(0,0,0,0.2); max-width: 90vw;
  }}
  .toast.visible {{ opacity: 1; }}
</style>
</head>
<body>
<header>
  <h1>LocalJobScout — Job Report</h1>
  <div class="meta">
    Generated {generated_at.strftime("%Y-%m-%d %H:%M UTC")}
    · {total_count} jobs across {len(source_counts)} sources
    · threshold {threshold:.3f}
  </div>
</header>

<div class="summary">
  <div class="stat"><strong>{total_count}</strong>total jobs</div>
  <div class="stat"><strong>{above_count}</strong>above threshold</div>
  <div class="stat"><strong>{notified_count}</strong>already notified</div>
  <div class="stat"><strong>{tracked_count}</strong>tracked applications</div>
  <div class="stat"><strong>{len(source_counts)}</strong>active sources</div>
</div>

<div class="controls">
  <input type="search" id="search" placeholder="Search title or company...">
  <span style="font-size:13px;color:#4b5563;">Sources:</span>
  {source_chips}
  <label>
    Threshold:
    <input type="range" id="threshold" min="0" max="0.5" step="0.005"
           value="{threshold:.3f}">
    <span id="threshold-value">{threshold:.3f}</span>
  </label>
  <label>
    <input type="checkbox" id="above-only"> Above threshold only
  </label>
  <label>
    Status:
    <select id="status-filter">
      <option value="">all</option>
      <option value="__tracked__">any tracked</option>
      <option value="__untracked__">untracked</option>
      <option value="interested">interested</option>
      <option value="applied">applied</option>
      <option value="interviewed">interviewed</option>
      <option value="offered">offered</option>
      <option value="rejected">rejected</option>
      <option value="hidden">hidden</option>
    </select>
  </label>
</div>
<div id="toast" class="toast"></div>

<table id="jobs">
<thead>
  <tr>
    <th data-sort="rank">#</th>
    <th data-sort="score" data-sorted="desc">Score</th>
    <th>Flag</th>
    <th data-sort="source">Source</th>
    <th data-sort="title">Title</th>
    <th data-sort="company">Company</th>
    <th data-sort="location">Location</th>
    <th data-sort="posted">Posted</th>
    <th>Actions</th>
  </tr>
</thead>
<tbody id="job-rows">
{rows}
</tbody>
</table>

<script>
(() => {{
  const tbody = document.getElementById("job-rows");
  const rows = Array.from(tbody.querySelectorAll("tr.job-row"));
  const searchInput = document.getElementById("search");
  const thresholdInput = document.getElementById("threshold");
  const thresholdValue = document.getElementById("threshold-value");
  const aboveOnly = document.getElementById("above-only");
  const sourceToggles = Array.from(
    document.querySelectorAll("[data-source-filter]")
  );
  const statusFilter = document.getElementById("status-filter");
  const toast = document.getElementById("toast");

  const enabledSources = () => new Set(
    sourceToggles.filter(c => c.checked).map(c => c.dataset.sourceFilter)
  );

  function showToast(msg) {{
    toast.textContent = msg;
    toast.classList.add("visible");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(
      () => toast.classList.remove("visible"), 2200
    );
  }}

  document.querySelectorAll(".copy-cmd").forEach(btn => {{
    btn.addEventListener("click", async () => {{
      const cmd = btn.dataset.cmd;
      try {{
        await navigator.clipboard.writeText(cmd);
        btn.classList.add("copied");
        const original = btn.textContent;
        btn.textContent = "copied!";
        showToast("Copied: " + cmd);
        setTimeout(() => {{
          btn.classList.remove("copied");
          btn.textContent = original;
        }}, 1500);
      }} catch (e) {{
        showToast("Copy failed — command: " + cmd);
      }}
    }});
  }});

  function applyFilters() {{
    const query = searchInput.value.trim().toLowerCase();
    const threshold = parseFloat(thresholdInput.value);
    thresholdValue.textContent = threshold.toFixed(3);
    const onlyAbove = aboveOnly.checked;
    const sources = enabledSources();
    const statusSel = statusFilter.value;

    rows.forEach(row => {{
      const score = parseFloat(row.dataset.score);
      const source = row.dataset.source;
      const status = row.dataset.status || "";
      const text = row.dataset.search;
      const sourceOk = sources.has(source);
      const queryOk = !query || text.includes(query);
      const aboveOk = !onlyAbove || score >= threshold;
      let statusOk = true;
      if (statusSel === "__tracked__") statusOk = status !== "";
      else if (statusSel === "__untracked__") statusOk = status === "";
      else if (statusSel !== "") statusOk = status === statusSel;
      row.classList.toggle(
        "hidden", !(sourceOk && queryOk && aboveOk && statusOk)
      );
      row.classList.toggle("above-threshold", score >= threshold);
      const badge = row.querySelector(".badge.above, .badge.below");
      if (badge) {{
        if (score >= threshold) {{
          badge.className = "badge above"; badge.textContent = "✓ above";
        }} else {{
          badge.className = "badge below"; badge.textContent = "below";
        }}
      }}
    }});
  }}

  function sortBy(key, dir) {{
    const factor = dir === "asc" ? 1 : -1;
    const ranked = rows.slice().sort((a, b) => {{
      let av, bv;
      if (key === "score" || key === "rank") {{
        av = parseFloat(a.dataset.score);
        bv = parseFloat(b.dataset.score);
        // rank sort = numeric on score (asc rank = desc score)
        if (key === "rank") {{ av = parseInt(a.cells[0].textContent, 10);
                                bv = parseInt(b.cells[0].textContent, 10); }}
      }} else {{
        const colIndex = {{
          source: 3, title: 4, company: 5, location: 6, posted: 7
        }}[key];
        av = a.cells[colIndex].textContent.trim().toLowerCase();
        bv = b.cells[colIndex].textContent.trim().toLowerCase();
      }}
      if (av < bv) return -1 * factor;
      if (av > bv) return  1 * factor;
      return 0;
    }});
    tbody.innerHTML = "";
    ranked.forEach(r => tbody.appendChild(r));
  }}

  document.querySelectorAll("th[data-sort]").forEach(th => {{
    th.addEventListener("click", () => {{
      const key = th.dataset.sort;
      const current = th.dataset.sorted;
      const next = current === "desc" ? "asc" : "desc";
      document.querySelectorAll("th[data-sort]").forEach(
        x => x.removeAttribute("data-sorted")
      );
      th.dataset.sorted = next;
      sortBy(key, next);
    }});
  }});

  [
    searchInput, thresholdInput, aboveOnly, statusFilter, ...sourceToggles
  ].forEach(el => {{
    el.addEventListener("input", applyFilters);
    el.addEventListener("change", applyFilters);
  }});
  applyFilters();
}})();
</script>
</body>
</html>
"""


def write_report(
    scored: list[tuple[Job, float]],
    threshold: float,
    output_path: Path,
    *,
    open_in_browser: bool = True,
) -> Path:
    """Render the report and write it to `output_path`. Returns the absolute
    path so the caller can print it. If `open_in_browser` is True, also
    fires `webbrowser.open(...)` on the resulting file URL."""
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_html(scored, threshold), encoding="utf-8"
    )
    if open_in_browser:
        try:
            webbrowser.open(output_path.as_uri())
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not open browser: %s", exc)
    return output_path
