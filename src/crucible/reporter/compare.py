"""Side-by-side comparison renderer — M2 PR 11.

Produces a single static HTML doc that places two ledger trees in two
columns. Useful for "greedy vs bfts-lite on the same example" demo-gate
comparisons.

Strict read-only: never writes to ledgers, never normalises config. If a
side has missing metadata (no metrics, no cost), the column renders
"n/a" rather than failing the whole comparison.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Sequence

from crucible.ledger import AttemptNode, TrialLedger
from crucible.reporter.html_tree import (
    _CSS,
    _best_node_id,
    _color_for,
    _format_cost,
    _render_summary,
    _render_tree,
)


def render_comparison_html(
    left_ledger_path: Path | str,
    right_ledger_path: Path | str,
    *,
    left_label: str,
    right_label: str,
    title: str = "Crucible Compare",
    left_metric_lookup: dict[str, float] | None = None,
    right_metric_lookup: dict[str, float] | None = None,
    left_direction: str | None = None,
    right_direction: str | None = None,
) -> str:
    """Render a side-by-side comparison as a self-contained HTML document.

    Args:
        left_ledger_path / right_ledger_path: paths to ledger.jsonl files.
        left_label / right_label: user-facing labels for each side
            (e.g. "greedy", "bfts-lite", or a tag name).
        title: top-of-page title.
        left_metric_lookup / right_metric_lookup: per-side `attempt_id →
            metric_value` maps. Independent because two runs may use
            different metric scales (rare but legal).
        left_direction / right_direction: per-side metric direction
            ("maximize" / "minimize"). If both sides agree (and both are
            non-None), a Δ best-metric line is rendered. If they differ
            or either is None, the Δ is omitted.

    Returns:
        Complete HTML document (UTF-8 string).

    Missing-data behaviour: an unreadable ledger raises (file errors are
    not silent). An empty ledger renders an "(empty)" panel on that
    side. Missing metric_lookup → no metric line on cards, no Δ.
    """
    left_nodes = _safe_load_nodes(left_ledger_path)
    right_nodes = _safe_load_nodes(right_ledger_path)

    left_metrics = left_metric_lookup or {}
    right_metrics = right_metric_lookup or {}

    left_best_id = _best_node_id(left_nodes, left_metrics, left_direction or "maximize")
    right_best_id = _best_node_id(right_nodes, right_metrics, right_direction or "maximize")

    delta_line = _render_delta(
        left_best_id, right_best_id,
        left_metrics, right_metrics,
        left_direction, right_direction,
    )

    left_section = _render_side(
        left_label, left_nodes, left_metrics, left_best_id,
    )
    right_section = _render_side(
        right_label, right_nodes, right_metrics, right_best_id,
    )

    return _COMPARE_PAGE_TEMPLATE.format(
        title=html.escape(title),
        css=_CSS + _COMPARE_CSS,
        delta=delta_line,
        left_section=left_section,
        right_section=right_section,
        generated_at=html.escape(datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_load_nodes(path: Path | str) -> Sequence[AttemptNode]:
    """Load ledger nodes; ledger-read errors propagate (don't silently zero)."""
    return TrialLedger(Path(path)).all_nodes()


def _render_side(
    label: str,
    nodes: Sequence[AttemptNode],
    metric_lookup: dict[str, float],
    best_id: str | None,
) -> str:
    """Render one column: header label + summary pills + tree."""
    safe_label = html.escape(label)
    if not nodes:
        return (
            f'<section class="side">'
            f'<h2 class="side-label">{safe_label}</h2>'
            f'<div class="empty">(no attempts in this ledger)</div>'
            f'</section>'
        )
    summary = _render_summary(nodes, metric_lookup, best_id)
    cards = _render_tree(nodes, best_id, metric_lookup)
    return (
        f'<section class="side">'
        f'<h2 class="side-label">{safe_label}</h2>'
        f'{summary}'
        f'<main class="side-cards">{cards}</main>'
        f'</section>'
    )


def _render_delta(
    left_best_id: str | None,
    right_best_id: str | None,
    left_metrics: dict[str, float],
    right_metrics: dict[str, float],
    left_direction: str | None,
    right_direction: str | None,
) -> str:
    """Render the Δ line — only when both directions agree and both metrics
    are available. Otherwise return an empty string (no auto-verdict)."""
    if left_best_id is None or right_best_id is None:
        return ""
    if left_direction is None or right_direction is None:
        return ""
    if left_direction != right_direction:
        return ""
    left_v = left_metrics.get(left_best_id)
    right_v = right_metrics.get(right_best_id)
    if left_v is None or right_v is None:
        return ""
    delta = right_v - left_v
    sign = "+" if delta >= 0 else ""
    return (
        f'<div class="delta">'
        f'left best: <code>{left_v}</code>'
        f' &nbsp;|&nbsp; '
        f'right best: <code>{right_v}</code>'
        f' &nbsp;|&nbsp; '
        f'Δ (right − left): <code>{sign}{delta}</code>'
        f' <span class="delta-note">(arithmetic delta only — no winner verdict)</span>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Compare-specific CSS + page template (single-view CSS reused as base)
# ---------------------------------------------------------------------------


_COMPARE_CSS = """
.compare-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  align-items: start;
}
.side {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  min-width: 0;  /* permit grid cell to shrink */
}
.side-label {
  margin: 0 0 12px 0;
  font-size: 18px;
  color: #1a237e;
  border-bottom: 2px solid #e8eaf6;
  padding-bottom: 6px;
}
.side-cards { padding-top: 8px; }
.delta {
  background: #fff;
  border-radius: 8px;
  padding: 12px 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  margin-bottom: 16px;
  font-size: 14px;
  color: #424242;
}
.delta-note {
  color: #757575;
  font-size: 12px;
  margin-left: 8px;
}
@media (max-width: 1100px) {
  .compare-grid { grid-template-columns: 1fr; }
}
"""


_COMPARE_PAGE_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}
.container {{ max-width: 1600px; }}
</style>
</head>
<body>
<div class="container">
  <h1>{title}</h1>
  <div class="generated">Generated {generated_at}</div>
  {delta}
  <div class="compare-grid">
    {left_section}
    {right_section}
  </div>
</div>
</body>
</html>
"""
