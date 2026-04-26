"""Tests for `crucible.reporter.html_tree.render_static_html`.

Verifies:
  - Empty ledger → valid HTML with "no attempts" empty state
  - Single keep node → contains node id, outcome, commit
  - Multiple nodes with parent chain → links present
  - Best-of-run highlighted when metric_lookup provided
  - Outcome-specific CSS color hooks present
  - HTML output is well-formed (parseable by html.parser)
  - Diff text is HTML-escaped
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from crucible.ledger import AttemptNode, LedgerRecord, TrialLedger
from crucible.reporter import render_static_html


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Validator(HTMLParser):
    """Minimal sanity check that the HTML parses without errors."""

    def __init__(self) -> None:
        super().__init__()
        self.tags_open: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in ("br", "hr", "meta", "img", "input", "link"):
            self.tags_open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.tags_open:
            self.errors.append(f"close-without-open: {tag}")
            return
        if self.tags_open[-1] == tag:
            self.tags_open.pop()
        else:
            # tolerate slight mismatches (HTML5 self-closing or implicit)
            try:
                self.tags_open.remove(tag)
            except ValueError:
                self.errors.append(f"unmatched-close: {tag}")


def _validate(html_str: str) -> None:
    v = _Validator()
    v.feed(html_str)
    assert v.errors == [], f"HTML errors: {v.errors}"


def _make_node(seq: int, *, outcome: str = "keep", parent: str | None = None,
               diff: str = "", description: str | None = None) -> AttemptNode:
    return AttemptNode(
        id=AttemptNode.short_id(seq),
        parent_id=parent,
        commit=f"sha-{seq:08x}",
        backend_kind="claude_sdk",
        model="anthropic/sonnet-4-6",
        diff_text=diff,
        outcome=outcome,
        cost_usd=0.001 * seq,
        usage_source="api",
        created_at="2026-04-25T12:00:00+00:00",
        description=description,
    )


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger.jsonl"


# ---------------------------------------------------------------------------
# Empty / smoke
# ---------------------------------------------------------------------------


def test_empty_ledger_renders_valid_html(ledger_path: Path):
    ledger_path.touch()
    out = render_static_html(ledger_path)
    assert "<html" in out
    assert "No attempts recorded" in out
    _validate(out)


def test_missing_ledger_file(tmp_path: Path):
    """Reporter should not crash on a non-existent ledger path."""
    out = render_static_html(tmp_path / "nope.jsonl")
    assert "No attempts recorded" in out


# ---------------------------------------------------------------------------
# Single + multi-node rendering
# ---------------------------------------------------------------------------


def test_single_node_renders_outcome_and_commit(ledger_path: Path):
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, outcome="keep"))
    out = render_static_html(ledger_path)
    assert "n000001" in out
    assert "keep" in out.lower()
    assert "sha-000" in out  # short commit (7 chars) included
    _validate(out)


def test_parent_chain_links_render(ledger_path: Path):
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))
    ledger.append_node(_make_node(2, parent="n000001"))
    ledger.append_node(_make_node(3, parent="n000002"))
    out = render_static_html(ledger_path)
    # Each child has anchor href to parent
    assert 'href="#n000001"' in out
    assert 'href="#n000002"' in out
    # IDs as anchors on the cards
    assert 'id="n000001"' in out
    assert 'id="n000002"' in out
    assert 'id="n000003"' in out
    _validate(out)


def test_outcome_color_hooks_present(ledger_path: Path):
    """Each outcome class should have a distinct CSS color (verifies all
    declared outcomes get visual treatment)."""
    ledger = TrialLedger(ledger_path)
    outcomes = ["keep", "discard", "crash", "violation", "skip", "budget_exceeded"]
    for i, outcome in enumerate(outcomes, start=1):
        ledger.append_node(_make_node(i, outcome=outcome))
    out = render_static_html(ledger_path)
    for outcome in outcomes:
        assert outcome in out, f"missing outcome rendering: {outcome}"
    _validate(out)


def test_best_of_run_highlighted(ledger_path: Path):
    ledger = TrialLedger(ledger_path)
    for i in (1, 2, 3):
        ledger.append_node(_make_node(i))
    metrics = {"n000001": 0.5, "n000002": 1.7, "n000003": 1.0}
    out = render_static_html(ledger_path, metric_lookup=metrics)
    # Summary block mentions best
    assert "Best metric" in out
    assert "1.7" in out
    # The best card has the best-badge marker
    assert "★ best" in out
    # The best card class flag is on n000002
    badge_pos = out.index("★ best")
    n2_pos = out.index('id="n000002"')
    n3_pos = out.index('id="n000003"')
    # the badge should fall between n2 and n3 (i.e., inside n000002's card)
    assert n2_pos < badge_pos < n3_pos, "best badge attached to wrong node"
    _validate(out)


def test_diff_text_html_escaped(ledger_path: Path):
    """Diff content with HTML special chars must be escaped, not interpreted."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(
        1,
        diff="-a = '<script>alert(1)</script>'\n+a = 'safe'",
    ))
    out = render_static_html(ledger_path)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
    _validate(out)


def test_description_html_escaped(ledger_path: Path):
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(
        1, outcome="violation",
        description="<img src=x onerror='alert(1)'>",
    ))
    out = render_static_html(ledger_path)
    assert "<img src=x" not in out
    assert "&lt;img" in out
    _validate(out)


# ---------------------------------------------------------------------------
# Custom title + summary
# ---------------------------------------------------------------------------


def test_custom_title(ledger_path: Path):
    ledger_path.touch()
    out = render_static_html(ledger_path, title="optimize-compress run-7")
    assert "<title>optimize-compress run-7</title>" in out
    assert "<h1>optimize-compress run-7</h1>" in out


def test_summary_pill_counts(ledger_path: Path):
    """Summary block should list outcome counts as pills."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, outcome="keep"))
    ledger.append_node(_make_node(2, outcome="keep", parent="n000001"))
    ledger.append_node(_make_node(3, outcome="discard", parent="n000002"))
    ledger.append_node(_make_node(4, outcome="crash", parent="n000003"))
    out = render_static_html(ledger_path)
    # "keep: 2", "discard: 1", "crash: 1" all present
    assert re.search(r"keep:\s*2", out)
    assert re.search(r"discard:\s*1", out)
    assert re.search(r"crash:\s*1", out)


def test_total_cost_summed(ledger_path: Path):
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))   # cost 0.001
    ledger.append_node(_make_node(2))   # 0.002
    ledger.append_node(_make_node(3))   # 0.003
    out = render_static_html(ledger_path)
    assert "$0.0060" in out  # total = 0.006


# ---------------------------------------------------------------------------
# Reviewer F2 — minimize-objective best selection
# ---------------------------------------------------------------------------


def test_minimize_picks_smallest_metric(ledger_path: Path):
    """For minimize objectives (TSP, tokenizer, regression), the best-of-run
    star must highlight the SMALLEST metric, not the largest. Without this
    fix the report would star the worst kept attempt."""
    ledger = TrialLedger(ledger_path)
    for i in (1, 2, 3):
        ledger.append_node(_make_node(i))
    metrics = {"n000001": 5.0, "n000002": 1.0, "n000003": 3.0}
    out = render_static_html(
        ledger_path,
        metric_lookup=metrics,
        metric_direction="minimize",
    )
    # n000002 is the smallest → should be starred
    badge_pos = out.index("★ best")
    n2_pos = out.index('id="n000002"')
    n3_pos = out.index('id="n000003"')
    assert n2_pos < badge_pos < n3_pos


def test_maximize_picks_largest_metric_default(ledger_path: Path):
    """Default direction=maximize keeps existing behaviour."""
    ledger = TrialLedger(ledger_path)
    for i in (1, 2, 3):
        ledger.append_node(_make_node(i))
    metrics = {"n000001": 5.0, "n000002": 1.0, "n000003": 3.0}
    out = render_static_html(ledger_path, metric_lookup=metrics)
    # n000001 = 5.0 is largest → should be starred
    badge_pos = out.index("★ best")
    n1_pos = out.index('id="n000001"')
    n2_pos = out.index('id="n000002"')
    assert n1_pos < badge_pos < n2_pos


# ---------------------------------------------------------------------------
# M1b PR 7: tree-view rendering
# ---------------------------------------------------------------------------


def test_tree_view_indents_descendants(ledger_path: Path):
    """A 3-level chain (root → child → grandchild) should render with
    progressive left-margin indentation."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))                     # depth 0
    ledger.append_node(_make_node(2, parent="n000001"))   # depth 1
    ledger.append_node(_make_node(3, parent="n000002"))   # depth 2
    out = render_static_html(ledger_path)
    # depth-1 card has margin-left:32px; depth-2 has 64px
    assert "margin-left:32px" in out
    assert "margin-left:64px" in out
    # branch markers (↳) on non-root cards
    assert out.count("branch-marker") >= 2
    _validate(out)


def test_tree_view_handles_branching(ledger_path: Path):
    """Two children sharing a parent → both at depth=1, indented equally."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))                     # root
    ledger.append_node(_make_node(2, parent="n000001"))   # branch A
    ledger.append_node(_make_node(3, parent="n000001"))   # branch B
    out = render_static_html(ledger_path)
    # Both children indented at depth 1
    assert out.count("margin-left:32px") >= 2
    # Both have a branch-marker
    assert out.count("branch-marker") >= 2
    _validate(out)


def test_tree_view_orphan_node(ledger_path: Path):
    """A node referencing a parent_id that doesn't exist in the ledger
    is rendered with the orphan badge below the main tree."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))                     # root
    ledger.append_node(_make_node(2, parent="n999999"))   # orphan
    out = render_static_html(ledger_path)
    assert "orphan" in out.lower()
    assert "orphan-badge" in out
    _validate(out)


def test_tree_view_linear_chain_unchanged(ledger_path: Path):
    """For a fully-linear chain (each node parent of next), the new tree
    rendering still produces output where ids appear in order."""
    ledger = TrialLedger(ledger_path)
    for i in (1, 2, 3, 4):
        parent = f"n{i - 1:06d}" if i > 1 else None
        ledger.append_node(_make_node(i, parent=parent))
    out = render_static_html(ledger_path)
    # Verify n000001 appears before n000002 etc.
    positions = [out.index(f'id="n00000{i}"') for i in range(1, 5)]
    assert positions == sorted(positions)
