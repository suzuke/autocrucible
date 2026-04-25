"""Crucible reporter — static HTML rendering of TrialLedger.

Per design doc v1.0-design-final.md §M1a deliverable 3 + §M3 (interactive).

M1a: linear-chain static HTML (vertical timeline, no JS). Self-contained,
no external dependencies — produces an offline-browsable file.
M1b will add tree-view styling for BFTS expansions.
M3 will add d3.js interactive expand/collapse.
"""

from crucible.reporter.html_tree import render_static_html

__all__ = ["render_static_html"]
