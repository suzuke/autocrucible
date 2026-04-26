"""Crucible reporter — HTML rendering of TrialLedger.

Per design doc v1.0-design-final.md §M1a deliverable 3 + §M3 (interactive).

M1a: linear-chain static HTML (vertical timeline, no JS). Self-contained,
no external dependencies — produces an offline-browsable file.
M1b: tree-view styling for BFTS expansions.
M2: side-by-side compare (`render_comparison_html`).
M3: d3.js interactive expand/collapse (`render_interactive_html`).
"""

from crucible.reporter.compare import render_comparison_html
from crucible.reporter.html_tree import render_static_html
from crucible.reporter.interactive import render_interactive_html

__all__ = [
    "render_static_html",
    "render_comparison_html",
    "render_interactive_html",
]
