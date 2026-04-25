"""Static HTML renderer for TrialLedger — M1a deliverable.

Reads `logs/run-<tag>/ledger.jsonl`, renders a self-contained HTML file
showing each AttemptNode as a card in a vertical timeline. No JS, no
external CSS — opens offline in any browser.

M1b will replace this linear layout with a tree view that branches on
BFTS expansions. M3 will add interactive d3.js drill-down.
"""

from __future__ import annotations

import html
import json
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from crucible.ledger import AttemptNode, TrialLedger


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_static_html(
    ledger_path: Path | str,
    *,
    title: str = "Crucible Postmortem",
    metric_lookup: dict[str, float] | None = None,
    metric_direction: str = "maximize",
) -> str:
    """Render the ledger at `ledger_path` as a self-contained HTML string.

    Args:
        ledger_path: path to logs/run-<tag>/ledger.jsonl
        title: title shown at the top of the HTML
        metric_lookup: optional mapping `attempt_id → metric_value` (from
            results-{tag}.jsonl or sealed EvalResult). When provided, the
            best-of-run is highlighted and metric values appear on each card.
        metric_direction: "maximize" (default) or "minimize". Determines which
            extreme of metric_lookup is starred as best-of-run. Critical for
            minimize-objective examples (TSP, tokenizer, regression, lm).

    Returns:
        Complete HTML document (UTF-8 string, ready to write or display).
    """
    ledger = TrialLedger(Path(ledger_path))
    nodes = ledger.all_nodes()
    return _render(
        nodes,
        title=title,
        metric_lookup=metric_lookup or {},
        metric_direction=metric_direction,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


_OUTCOME_COLORS: dict[str, str] = {
    "keep":            ("#1b5e20", "#a5d6a7"),  # dark green, light green bg
    "discard":         ("#bf360c", "#ffccbc"),
    "crash":           ("#b71c1c", "#ffcdd2"),
    "violation":       ("#4a148c", "#e1bee7"),
    "skip":            ("#37474f", "#cfd8dc"),
    "budget_exceeded": ("#33691e", "#dcedc8"),
    "fatal":           ("#000000", "#ff8a80"),
}

_DEFAULT_COLORS = ("#212121", "#eeeeee")


def _color_for(outcome: str) -> tuple[str, str]:
    return _OUTCOME_COLORS.get(outcome, _DEFAULT_COLORS)


def _format_cost(cost: float | None) -> str:
    if cost is None:
        return "—"
    return f"${cost:.4f}"


def _short_commit(c: str) -> str:
    return c[:7] if c else "—"


def _truncate(s: str, n: int = 200) -> str:
    if not s:
        return ""
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _format_iso(ts: str) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return ts


def _render(
    nodes: Sequence[AttemptNode],
    *,
    title: str,
    metric_lookup: dict[str, float],
    metric_direction: str = "maximize",
) -> str:
    if not nodes:
        return _empty_html(title)

    best_id = _best_node_id(nodes, metric_lookup, metric_direction)
    cards = "\n".join(_render_card(n, best_id, metric_lookup) for n in nodes)
    summary = _render_summary(nodes, metric_lookup, best_id)

    return _PAGE_TEMPLATE.format(
        title=html.escape(title),
        css=_CSS,
        summary=summary,
        cards=cards,
        generated_at=html.escape(datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")),
    )


def _empty_html(title: str) -> str:
    return _PAGE_TEMPLATE.format(
        title=html.escape(title),
        css=_CSS,
        summary='<div class="empty">No attempts recorded yet.</div>',
        cards="",
        generated_at=html.escape(datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")),
    )


def _best_node_id(nodes: Sequence[AttemptNode],
                  metric_lookup: dict[str, float],
                  metric_direction: str = "maximize") -> str | None:
    """Pick the best-metric kept node, honouring metric_direction.

    For maximize: argmax(metric). For minimize: argmin(metric). Critical for
    minimize-objective examples (TSP, tokenizer, regression, lm).
    Returns None if no kept node has a recorded metric.
    """
    candidates = [
        (metric_lookup.get(n.id), n)
        for n in nodes
        if n.outcome == "keep" and n.id in metric_lookup
    ]
    if not candidates:
        return None
    chooser = min if metric_direction == "minimize" else max
    return chooser(candidates, key=lambda pair: pair[0])[1].id


def _render_summary(nodes: Sequence[AttemptNode],
                    metric_lookup: dict[str, float],
                    best_id: str | None = None) -> str:
    by_outcome: dict[str, int] = {}
    for n in nodes:
        by_outcome[n.outcome] = by_outcome.get(n.outcome, 0) + 1

    pills = []
    for outcome in ("keep", "discard", "crash", "violation", "skip",
                    "budget_exceeded", "fatal"):
        count = by_outcome.get(outcome, 0)
        if count == 0:
            continue
        fg, bg = _color_for(outcome)
        pills.append(
            f'<span class="pill" style="color:{fg};background:{bg}">'
            f'{html.escape(outcome)}: {count}</span>'
        )

    best_line = ""
    if best_id is not None:
        v = metric_lookup.get(best_id)
        best_line = (
            f'<div class="best">Best metric: <code>{v}</code> '
            f'(node <a href="#{best_id}">{best_id}</a>)</div>'
        )

    total_cost = sum((n.cost_usd or 0.0) for n in nodes)
    cost_line = f'<div class="cost">Total cost: {_format_cost(total_cost)}</div>'

    return (
        f'<div class="summary">'
        f'<div class="counts">{"".join(pills)}</div>'
        f'{best_line}{cost_line}'
        f'</div>'
    )


def _render_card(n: AttemptNode, best_id: str | None,
                 metric_lookup: dict[str, float]) -> str:
    fg, bg = _color_for(n.outcome)
    is_best = (best_id is not None and n.id == best_id)
    badge = '<span class="best-badge">★ best</span>' if is_best else ""

    metric_line = ""
    if n.id in metric_lookup:
        metric_line = (
            f'<div class="metric"><strong>metric:</strong> '
            f'<code>{metric_lookup[n.id]}</code></div>'
        )

    parent_line = (
        f'<a class="parent-link" href="#{n.parent_id}">{n.parent_id}</a>'
        if n.parent_id else "(root)"
    )

    diff_block = ""
    if n.diff_text:
        diff_block = (
            f'<details><summary>diff ({len(n.diff_text)} chars'
            f'{ " — see " + html.escape(n.diff_ref) if n.diff_ref else "" })</summary>'
            f'<pre class="diff">{html.escape(n.diff_text)}</pre></details>'
        )

    description_line = ""
    if n.description:
        description_line = (
            f'<div class="description"><em>{html.escape(_truncate(n.description, 300))}</em></div>'
        )

    backend_line = (
        f'<span class="meta-item">backend: <code>{html.escape(n.backend_kind)}</code></span>'
        f'<span class="meta-item">model: <code>{html.escape(n.model)}</code></span>'
    )

    return f"""
<article id="{html.escape(n.id)}" class="card{' card-best' if is_best else ''}"
         style="border-left-color:{fg}">
  <header class="card-header" style="background:{bg};color:{fg}">
    <span class="node-id">{html.escape(n.id)}</span>
    <span class="outcome">{html.escape(n.outcome)}</span>
    {badge}
  </header>
  <div class="card-body">
    <div class="meta">
      <span class="meta-item">parent: {parent_line}</span>
      <span class="meta-item">commit: <code>{_short_commit(n.commit)}</code></span>
      <span class="meta-item">cost: {_format_cost(n.cost_usd)}</span>
      <span class="meta-item">created: {_format_iso(n.created_at)}</span>
      {backend_line}
    </div>
    {metric_line}
    {description_line}
    {diff_block}
  </div>
</article>
"""


# ---------------------------------------------------------------------------
# Page template + CSS (self-contained, no external assets)
# ---------------------------------------------------------------------------


_CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 24px; background: #fafafa; color: #212121;
}
.container { max-width: 960px; margin: 0 auto; }
h1 { margin: 0 0 8px 0; font-size: 28px; }
.generated { color: #757575; font-size: 13px; margin-bottom: 24px; }

.summary {
  background: #fff; border-radius: 8px; padding: 16px 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  margin-bottom: 24px;
}
.counts { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.pill {
  display: inline-block; padding: 4px 10px; border-radius: 12px;
  font-size: 13px; font-weight: 500;
}
.best { color: #1b5e20; font-weight: 500; margin-top: 4px; }
.cost { color: #424242; font-size: 14px; }
.empty {
  background: #fff; border-radius: 8px; padding: 24px;
  text-align: center; color: #757575;
}

.card {
  background: #fff; border-radius: 8px; margin-bottom: 12px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.08);
  border-left: 4px solid #e0e0e0;
  overflow: hidden;
}
.card-best { box-shadow: 0 0 0 2px #fbc02d, 0 1px 3px rgba(0,0,0,0.1); }
.card-header {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 16px; font-size: 14px;
}
.node-id { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-weight: 600; }
.outcome { font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
.best-badge { margin-left: auto; color: #f9a825; font-weight: 600; }

.card-body { padding: 12px 16px; }
.meta { color: #616161; font-size: 13px; display: flex; flex-wrap: wrap; gap: 12px; }
.meta-item { white-space: nowrap; }
.parent-link { color: #1976d2; text-decoration: none; }
.parent-link:hover { text-decoration: underline; }
code { background: #f5f5f5; padding: 1px 5px; border-radius: 3px;
       font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
.metric { margin-top: 8px; font-size: 14px; }
.description { margin-top: 6px; color: #424242; font-size: 13px; }

details { margin-top: 10px; }
summary { cursor: pointer; color: #1976d2; font-size: 13px; user-select: none; }
.diff {
  background: #263238; color: #eceff1; padding: 10px 12px;
  border-radius: 4px; overflow-x: auto; font-size: 12px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  white-space: pre; max-height: 400px; overflow-y: auto;
}
.diff::selection { background: #ffd54f; color: #000; }
"""


_PAGE_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="container">
  <h1>{title}</h1>
  <div class="generated">Generated {generated_at}</div>
  {summary}
  <main>
    {cards}
  </main>
</div>
</body>
</html>
"""
