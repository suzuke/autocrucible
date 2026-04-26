"""Interactive d3 tree-view renderer — M3 PR 15.

Same I/O contract as `html_tree.render_static_html`, but produces a
self-contained interactive HTML document with d3.js v7 inlined.

Reviewer round 1 constraints (folded in):
  - d3 vendored at `_vendor/d3.v7.9.0.min.js`; loaded via
    `importlib.resources` so the path works in dev + installed forms.
  - Embedded JSON is escaped against `</script>` injection (XSS via
    user-controlled fields like `description` / `commit message`).
  - Default expansion collapses children beyond depth 2 — doom-loop
    runs routinely produce 100+ nodes; full-expand on load is unusable.
  - "Expand all" / "Collapse all" affordances in the UI for users who
    want the full tree.
  - Static reporter (`render_static_html`) is NOT touched; output is
    byte-identical to before this PR.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from crucible.ledger import AttemptNode, TrialLedger
from crucible.reporter._vendor import D3_VERSION, read_d3_source
from crucible.reporter.html_tree import (
    _OUTCOME_COLORS,
    _best_node_id,
    _DEFAULT_COLORS,
    _format_cost,
    _format_iso,
    _short_commit,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_interactive_html(
    ledger_path: Path | str,
    *,
    title: str = "Crucible Postmortem (interactive)",
    metric_lookup: dict[str, float] | None = None,
    metric_direction: str = "maximize",
) -> str:
    """Render the ledger at `ledger_path` as an interactive d3 tree HTML.

    Args:
        ledger_path: path to logs/run-<tag>/ledger.jsonl
        title: shown at the top of the document
        metric_lookup: optional {attempt_id: metric_value} from results-{tag}.jsonl;
            best-of-run is highlighted when provided
        metric_direction: "maximize" / "minimize" — picks best-of-run

    Returns:
        Complete HTML document string. Self-contained: opens offline
        in any modern browser, no network calls.
    """
    ledger = TrialLedger(Path(ledger_path))
    nodes = ledger.all_nodes()
    metric_lookup = metric_lookup or {}

    if not nodes:
        return _empty_html(title)

    best_id = _best_node_id(nodes, metric_lookup, metric_direction)
    tree_data = _build_tree_data(nodes, metric_lookup, best_id)

    summary = _render_summary(nodes, metric_lookup, best_id)
    # M3 PR 17: same SSOT banner renderer as static reporter
    from crucible.reporter._banners import render_banners_html
    metadatas = [
        {
            "isolation": n.isolation,
            "compliance_report_path": n.compliance_report_path,
            "backend_kind": n.backend_kind,
        }
        for n in nodes
    ]
    banners = render_banners_html(metadatas)

    return _PAGE_TEMPLATE.format(
        title=html.escape(title),
        d3_version=D3_VERSION,
        d3_source=read_d3_source(),
        css=_CSS,
        summary=banners + summary,
        tree_data=_safe_json_for_script(tree_data),
        generated_at=html.escape(
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        ),
    )


# ---------------------------------------------------------------------------
# JSON safety: prevent `</script>` XSS via user-controlled fields
# ---------------------------------------------------------------------------


def _safe_json_for_script(data: Any) -> str:
    """Serialise `data` to JSON safe to embed inside an HTML `<script>` tag.

    Reviewer round 1 gap #2: `json.dumps` does NOT escape `</script>`
    sequences. A node `description` containing `</script><img src=x
    onerror=alert(1)>` would break out of the script tag and execute
    attacker-controlled HTML. Standard fix: escape the closing-tag
    pattern + line/paragraph separators that some browsers treat as
    line terminators inside scripts.
    """
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        raw
        .replace("</", "<\\/")          # closes-tag escape (the critical one)
        .replace(" ", "\\u2028")   # line separator (script-breaking on some engines)
        .replace(" ", "\\u2029")   # paragraph separator
    )


# ---------------------------------------------------------------------------
# Tree-data shape (consumed by the embedded d3 script)
# ---------------------------------------------------------------------------


def _build_tree_data(
    nodes: Sequence[AttemptNode],
    metric_lookup: dict[str, float],
    best_id: str | None,
) -> dict:
    """Produce the JSON shape the embedded d3 script consumes.

    Output is a single root with `children` populated via a DFS over
    `parent_id`. Nodes with `parent_id` set but parent missing from the
    ledger become "orphan" children of a synthetic root, mirroring the
    static reporter's orphan handling.
    """
    by_parent: dict[str | None, list[AttemptNode]] = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)
    for siblings in by_parent.values():
        siblings.sort(key=lambda n: (n.id, n.created_at))

    visited: set[str] = set()

    def serialize(n: AttemptNode, *, orphan: bool) -> dict:
        visited.add(n.id)
        fg, bg = _OUTCOME_COLORS.get(n.outcome, _DEFAULT_COLORS)
        children = [serialize(c, orphan=False) for c in by_parent.get(n.id, [])]
        return {
            "id": n.id,
            "outcome": n.outcome,
            "outcome_fg": fg,
            "outcome_bg": bg,
            "is_best": (best_id is not None and n.id == best_id),
            "is_orphan": orphan,
            "parent_id": n.parent_id,
            "commit": _short_commit(n.commit),
            "metric": metric_lookup.get(n.id),
            "cost_usd": n.cost_usd,
            "created_at": n.created_at,
            "description": n.description or "",
            "model": n.model or "",
            "backend_kind": n.backend_kind or "",
            "diff_size": len(n.diff_text or ""),
            "children": children,
        }

    roots = [serialize(n, orphan=False) for n in by_parent.get(None, [])]

    # Orphans (parent_id set but parent missing). Render as siblings of
    # the real roots, marked is_orphan=True so the d3 script can style.
    for n in nodes:
        if n.id not in visited:
            roots.append(serialize(n, orphan=True))

    return {
        "name": "ROOT",   # synthetic; d3 hierarchy wants a single root
        "synthetic_root": True,
        "children": roots,
    }


# ---------------------------------------------------------------------------
# Summary header (above the d3 view)
# ---------------------------------------------------------------------------


def _render_summary(
    nodes: Sequence[AttemptNode],
    metric_lookup: dict[str, float],
    best_id: str | None,
) -> str:
    by_outcome: dict[str, int] = {}
    for n in nodes:
        by_outcome[n.outcome] = by_outcome.get(n.outcome, 0) + 1
    pills = []
    for outcome in (
        "keep", "discard", "crash", "violation", "skip",
        "budget_exceeded", "fatal",
    ):
        count = by_outcome.get(outcome, 0)
        if count == 0:
            continue
        fg, bg = _OUTCOME_COLORS.get(outcome, _DEFAULT_COLORS)
        pills.append(
            f'<span class="pill" style="color:{fg};background:{bg}">'
            f'{html.escape(outcome)}: {count}</span>'
        )
    best_line = ""
    if best_id is not None:
        v = metric_lookup.get(best_id)
        best_line = (
            f'<div class="best">Best metric: <code>{v}</code> '
            f'(node <code>{html.escape(best_id)}</code>)</div>'
        )
    total_cost = sum((n.cost_usd or 0.0) for n in nodes)
    cost_line = f'<div class="cost">Total cost: {_format_cost(total_cost)}</div>'
    iter_count_line = f'<div class="iter-count">Iterations: <strong>{len(nodes)}</strong></div>'
    return (
        f'<div class="summary">'
        f'<div class="counts">{"".join(pills)}</div>'
        f'{iter_count_line}{best_line}{cost_line}'
        f'</div>'
    )


def _empty_html(title: str) -> str:
    return _PAGE_TEMPLATE.format(
        title=html.escape(title),
        d3_version=D3_VERSION,
        d3_source="",   # no d3 needed for empty state
        css=_CSS,
        summary='<div class="empty">No attempts recorded yet.</div>',
        tree_data="null",
        generated_at=html.escape(
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        ),
    )


# ---------------------------------------------------------------------------
# CSS + page template
# ---------------------------------------------------------------------------


_CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 0; background: #fafafa; color: #212121;
}
.container { max-width: 1400px; margin: 0 auto; padding: 24px; }
h1 { margin: 0 0 8px 0; font-size: 24px; }
.generated { color: #757575; font-size: 13px; margin-bottom: 16px; }
.toolbar {
  display: flex; gap: 8px; margin: 12px 0;
  flex-wrap: wrap;
}
.toolbar button {
  background: #fff; border: 1px solid #bdbdbd; border-radius: 6px;
  padding: 6px 12px; font-size: 13px; cursor: pointer;
  font-family: inherit;
}
.toolbar button:hover { background: #eeeeee; border-color: #757575; }

.summary {
  background: #fff; border-radius: 8px; padding: 12px 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  margin-bottom: 16px;
}
.counts { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.pill {
  display: inline-block; padding: 3px 9px; border-radius: 12px;
  font-size: 12px; font-weight: 500;
}
.iter-count { color: #424242; font-size: 13px; margin-top: 4px; }
.best { color: #1b5e20; font-size: 13px; }
.cost { color: #424242; font-size: 13px; }
.empty {
  background: #fff; border-radius: 8px; padding: 24px;
  text-align: center; color: #757575;
}

.layout {
  display: grid;
  grid-template-columns: 1fr 360px;
  gap: 16px;
  align-items: start;
}
@media (max-width: 1100px) {
  .layout { grid-template-columns: 1fr; }
}

.tree-panel {
  background: #fff; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  overflow: hidden;
  min-height: 600px;
}
.tree-svg {
  display: block;
  width: 100%;
  height: 700px;
  cursor: grab;
}
.tree-svg:active { cursor: grabbing; }

.detail-panel {
  background: #fff; border-radius: 8px; padding: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  position: sticky; top: 16px;
  max-height: 700px;
  overflow-y: auto;
}
.detail-panel h2 {
  margin: 0 0 8px 0; font-size: 15px;
  border-bottom: 2px solid #e0e0e0; padding-bottom: 4px;
}
.detail-empty { color: #757575; font-style: italic; font-size: 13px; }
.detail-row { margin-bottom: 8px; font-size: 13px; }
.detail-row .label {
  display: block; font-weight: 600; color: #616161;
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
}
.detail-row .value {
  display: block; color: #212121;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  word-break: break-word;
}
.detail-row .description-text {
  display: block; color: #424242; font-style: italic;
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 13px;
}

/* d3 node styling — fill from data.outcome_bg, stroke from outcome_fg */
.d3-node circle {
  stroke-width: 2px;
  cursor: pointer;
  transition: r 0.15s;
}
.d3-node:hover circle { r: 9; }
.d3-node.is-best circle {
  stroke: #f9a825 !important;
  stroke-width: 3px;
}
.d3-node.is-orphan circle {
  stroke-dasharray: 3 3;
}
.d3-node text {
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 11px;
  pointer-events: none;
  user-select: none;
}
.d3-node.has-collapsed text { font-weight: 600; }

.d3-link {
  fill: none;
  stroke: #bdbdbd;
  stroke-width: 1.5px;
}

.attribution {
  color: #9e9e9e; font-size: 11px; margin-top: 16px;
  text-align: center;
}
"""


# Note: this template uses {{...}} to escape JS curly braces inside .format()
# ONLY where Python format substitution should NOT happen. d3 inline source
# is interpolated verbatim — `read_d3_source()` already returns the JS as-is.
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
  <div class="generated">Generated {generated_at} · interactive (d3.js v{d3_version})</div>

  {summary}

  <div class="toolbar">
    <button id="btn-expand-all" type="button">Expand all</button>
    <button id="btn-collapse-all" type="button">Collapse to depth 2</button>
    <button id="btn-fit" type="button">Fit to view</button>
  </div>

  <div class="layout">
    <div class="tree-panel">
      <svg class="tree-svg" id="tree-svg"></svg>
    </div>

    <aside class="detail-panel">
      <h2>Node detail</h2>
      <div id="detail-body" class="detail-empty">
        Click a node to inspect.
      </div>
    </aside>
  </div>

  <div class="attribution">
    Embedded d3.js v{d3_version} · ISC License (Copyright 2010-2023 Mike Bostock).
    See `src/crucible/reporter/_vendor/LICENSE-d3.txt` for full text.
  </div>
</div>

<!-- Vendored d3.js v{d3_version} (ISC License, Copyright 2010-2023 Mike Bostock) -->
<script>
{d3_source}
</script>

<script id="tree-payload" type="application/json">
{tree_data}
</script>

<script>
(function () {{
  const payload = JSON.parse(document.getElementById("tree-payload").textContent);
  if (!payload) return;
  renderTree(payload);

  function renderTree(data) {{
    const svg = d3.select("#tree-svg");
    const svgNode = svg.node();
    if (!svgNode) return;
    const width = svgNode.clientWidth || 1000;
    const height = svgNode.clientHeight || 700;

    // Clear any prior render (we don't expect one, but defensive)
    svg.selectAll("*").remove();

    const g = svg.append("g");
    const linkG = g.append("g").attr("class", "d3-links");
    const nodeG = g.append("g").attr("class", "d3-nodes");

    // Pan & zoom
    const zoom = d3.zoom()
      .scaleExtent([0.2, 4])
      .on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoom);

    // Root: hide the synthetic root node when its only purpose is to
    // group multiple real roots / orphans. We just don't draw it.
    const root = d3.hierarchy(data);

    // Reviewer round 1 gap #3: default-collapse beyond depth 2.
    function collapseDeep(d) {{
      if (d.depth >= 2 && d.children) {{
        d._children = d.children;
        d.children = null;
      }}
      if (d.children) d.children.forEach(collapseDeep);
      if (d._children) d._children.forEach(collapseDeep);
    }}
    collapseDeep(root);

    const treeLayout = d3.tree().nodeSize([34, 220]);

    function update() {{
      treeLayout(root);

      // Translate so visible-node x range becomes centered vertically.
      // Reviewer round 2 polish: exclude synthetic root from the calc
      // so layouts with one real root + a few orphans don't off-center.
      let xMin = Infinity, xMax = -Infinity;
      root.each(d => {{
        if (d.data && d.data.synthetic_root) return;
        if (d.x < xMin) xMin = d.x;
        if (d.x > xMax) xMax = d.x;
      }});
      if (!isFinite(xMin)) {{ xMin = 0; xMax = 0; }}
      const tx = 80;  // left padding for first column
      const ty = ((height - 80) / 2) - ((xMax + xMin) / 2);

      // Filter out the synthetic root from rendered nodes/links; it
      // exists only to host multi-root + orphan children.
      const nodesArr = root.descendants().filter(d => !(d.data && d.data.synthetic_root));
      const linksArr = root.links().filter(l => !(l.source.data && l.source.data.synthetic_root));

      // Links
      const link = linkG.selectAll("path.d3-link").data(linksArr, d => d.target.data.id);
      link.enter().append("path")
        .attr("class", "d3-link")
        .merge(link)
        .attr("d", d3.linkHorizontal()
          .x(d => d.y + tx)
          .y(d => d.x + ty));
      link.exit().remove();

      // Nodes
      const node = nodeG.selectAll("g.d3-node").data(nodesArr, d => d.data.id);
      const nodeEnter = node.enter().append("g")
        .attr("class", d => {{
          const cls = ["d3-node"];
          if (d.data.is_best) cls.push("is-best");
          if (d.data.is_orphan) cls.push("is-orphan");
          if (d._children) cls.push("has-collapsed");
          return cls.join(" ");
        }})
        .attr("transform", d => `translate(${{d.y + tx}},${{d.x + ty}})`)
        .on("click", (event, d) => {{
          // Toggle children
          if (d.children) {{
            d._children = d.children;
            d.children = null;
          }} else if (d._children) {{
            d.children = d._children;
            d._children = null;
          }}
          showDetails(d.data);
          update();
        }});

      nodeEnter.append("circle")
        .attr("r", 7)
        .attr("fill", d => d.data.outcome_bg)
        .attr("stroke", d => d.data.outcome_fg);
      nodeEnter.append("text")
        .attr("dx", 12)
        .attr("dy", "0.35em")
        .text(d => {{
          let label = d.data.id;
          if (d.data.metric !== null && d.data.metric !== undefined) {{
            label += " · " + d.data.metric;
          }}
          if (d._children) label += " (+)";
          return label;
        }});

      node.exit().remove();

      // Refresh class on existing nodes (for has-collapsed toggle)
      node.attr("class", d => {{
        const cls = ["d3-node"];
        if (d.data.is_best) cls.push("is-best");
        if (d.data.is_orphan) cls.push("is-orphan");
        if (d._children) cls.push("has-collapsed");
        return cls.join(" ");
      }})
      .attr("transform", d => `translate(${{d.y + tx}},${{d.x + ty}})`);
      node.select("text").text(d => {{
        let label = d.data.id;
        if (d.data.metric !== null && d.data.metric !== undefined) {{
          label += " · " + d.data.metric;
        }}
        if (d._children) label += " (+)";
        return label;
      }});
    }}

    function showDetails(data) {{
      const body = document.getElementById("detail-body");
      body.classList.remove("detail-empty");
      const rows = [];
      function row(label, value, asDescription) {{
        if (value === undefined || value === null || value === "") return;
        rows.push(
          '<div class="detail-row"><span class="label">' + label + '</span>' +
          (asDescription
            ? '<span class="description-text">' + escapeHtml(String(value)) + '</span>'
            : '<span class="value">' + escapeHtml(String(value)) + '</span>') +
          '</div>'
        );
      }}
      row("Node id", data.id);
      row("Outcome", data.outcome);
      row("Parent", data.parent_id || "(root)");
      row("Commit", data.commit);
      row("Metric", data.metric);
      row("Cost (USD)", data.cost_usd);
      row("Created", data.created_at);
      row("Backend", data.backend_kind);
      row("Model", data.model);
      row("Diff size (chars)", data.diff_size);
      row("Description", data.description, true);
      body.innerHTML = rows.join("") || '<div class="detail-empty">(no metadata)</div>';
    }}

    function escapeHtml(s) {{
      return s.replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;")
              .replace(/'/g, "&#39;");
    }}

    // Toolbar
    document.getElementById("btn-expand-all").addEventListener("click", () => {{
      root.each(d => {{
        if (d._children) {{
          d.children = d._children;
          d._children = null;
        }}
      }});
      update();
    }});
    document.getElementById("btn-collapse-all").addEventListener("click", () => {{
      root.each(d => {{
        if (d.depth >= 2 && d.children) {{
          d._children = d.children;
          d.children = null;
        }}
      }});
      update();
    }});
    document.getElementById("btn-fit").addEventListener("click", () => {{
      svg.transition().duration(300).call(
        zoom.transform,
        d3.zoomIdentity.translate(0, 0).scale(1),
      );
    }});

    update();
  }}
}})();
</script>
</body>
</html>
"""
