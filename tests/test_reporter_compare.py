"""Tests for `crucible.reporter.compare.render_comparison_html` — M2 PR 11.

Verifies:
- Both side labels appear in output
- Both ledgers' node ids appear (left + right trees)
- Best-of-run badge appears on each side independently
- Δ line appears when both directions agree, both bests exist
- Δ line is suppressed when directions differ or are None
- Empty ledger on one side → "(no attempts)" panel, other side still renders
- HTML is well-formed
- Labels are HTML-escaped
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

from crucible.ledger import AttemptNode, TrialLedger
from crucible.reporter import render_comparison_html


# ---------------------------------------------------------------------------
# Helpers (parallel to test_reporter_html.py)
# ---------------------------------------------------------------------------


class _Validator(HTMLParser):
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


def _validate(html_str: str) -> None:
    p = _Validator()
    p.feed(html_str)
    assert not p.errors, f"HTML errors: {p.errors}"


def _make_node(seq: int, *, outcome: str = "keep",
               parent: str | None = None) -> AttemptNode:
    return AttemptNode(
        id=AttemptNode.short_id(seq),
        parent_id=parent,
        commit=f"sha-{seq:08x}",
        backend_kind="claude_sdk",
        model="anthropic/sonnet-4-6",
        outcome=outcome,
        cost_usd=0.001 * seq,
        created_at="2026-04-25T12:00:00+00:00",
    )


@pytest.fixture
def two_ledgers(tmp_path: Path) -> tuple[Path, Path]:
    left = tmp_path / "left.jsonl"
    right = tmp_path / "right.jsonl"
    return left, right


# ---------------------------------------------------------------------------
# Smoke + basic structure
# ---------------------------------------------------------------------------


def test_compare_renders_both_sides(two_ledgers: tuple[Path, Path]):
    left, right = two_ledgers
    lL = TrialLedger(left)
    lR = TrialLedger(right)
    lL.append_node(_make_node(1, outcome="keep"))
    lL.append_node(_make_node(2, parent="n000001", outcome="keep"))
    lR.append_node(_make_node(1, outcome="keep"))
    lR.append_node(_make_node(2, parent="n000001", outcome="discard"))

    out = render_comparison_html(
        left, right,
        left_label="greedy",
        right_label="bfts-lite",
    )

    # Both labels present
    assert "greedy" in out
    assert "bfts-lite" in out
    # Both ledgers' nodes present
    assert 'id="n000001"' in out
    assert 'id="n000002"' in out
    # Compare grid present
    assert "compare-grid" in out
    _validate(out)


def test_compare_empty_ledger_one_side(two_ledgers: tuple[Path, Path]):
    """Empty side renders 'no attempts' panel; other side renders normally."""
    left, right = two_ledgers
    left.touch()  # empty
    lR = TrialLedger(right)
    lR.append_node(_make_node(1, outcome="keep"))

    out = render_comparison_html(
        left, right,
        left_label="empty-run",
        right_label="real-run",
    )
    assert "no attempts" in out.lower()
    assert "real-run" in out
    assert 'id="n000001"' in out
    _validate(out)


# ---------------------------------------------------------------------------
# Best-of-run highlighting (per side, independent)
# ---------------------------------------------------------------------------


def test_compare_best_marker_per_side(two_ledgers: tuple[Path, Path]):
    left, right = two_ledgers
    lL = TrialLedger(left)
    lR = TrialLedger(right)
    for i in (1, 2):
        lL.append_node(_make_node(i, outcome="keep"))
    for i in (1, 2):
        lR.append_node(_make_node(i, outcome="keep"))

    out = render_comparison_html(
        left, right,
        left_label="A", right_label="B",
        left_metric_lookup={"n000001": 1.0, "n000002": 2.0},
        right_metric_lookup={"n000001": 5.0, "n000002": 3.0},
        left_direction="maximize", right_direction="maximize",
    )
    # Best in left = n000002 (2.0), best in right = n000001 (5.0)
    # Both should appear as "★ best" in the rendered output.
    assert out.count("★ best") == 2


# ---------------------------------------------------------------------------
# Δ line rendering rules
# ---------------------------------------------------------------------------


def test_delta_renders_when_directions_agree(two_ledgers: tuple[Path, Path]):
    left, right = two_ledgers
    lL = TrialLedger(left)
    lR = TrialLedger(right)
    lL.append_node(_make_node(1, outcome="keep"))
    lR.append_node(_make_node(1, outcome="keep"))

    out = render_comparison_html(
        left, right,
        left_label="A", right_label="B",
        left_metric_lookup={"n000001": 1.0},
        right_metric_lookup={"n000001": 1.7},
        left_direction="maximize", right_direction="maximize",
    )
    assert "Δ" in out
    assert "+0.7" in out or "0.7" in out  # right - left
    assert "no winner verdict" in out.lower()


def test_delta_omitted_when_directions_differ(two_ledgers: tuple[Path, Path]):
    left, right = two_ledgers
    lL = TrialLedger(left)
    lR = TrialLedger(right)
    lL.append_node(_make_node(1, outcome="keep"))
    lR.append_node(_make_node(1, outcome="keep"))

    out = render_comparison_html(
        left, right,
        left_label="A", right_label="B",
        left_metric_lookup={"n000001": 1.0},
        right_metric_lookup={"n000001": 1.7},
        left_direction="maximize", right_direction="minimize",
    )
    assert "Δ" not in out


def test_delta_omitted_when_direction_none(two_ledgers: tuple[Path, Path]):
    left, right = two_ledgers
    lL = TrialLedger(left)
    lR = TrialLedger(right)
    lL.append_node(_make_node(1, outcome="keep"))
    lR.append_node(_make_node(1, outcome="keep"))

    out = render_comparison_html(
        left, right,
        left_label="A", right_label="B",
        left_metric_lookup={"n000001": 1.0},
        right_metric_lookup={"n000001": 1.7},
        left_direction=None, right_direction=None,
    )
    assert "Δ" not in out


def test_delta_omitted_when_metric_lookup_empty(two_ledgers: tuple[Path, Path]):
    left, right = two_ledgers
    lL = TrialLedger(left)
    lR = TrialLedger(right)
    lL.append_node(_make_node(1, outcome="keep"))
    lR.append_node(_make_node(1, outcome="keep"))

    out = render_comparison_html(
        left, right,
        left_label="A", right_label="B",
        # no metric_lookup provided
        left_direction="maximize", right_direction="maximize",
    )
    assert "Δ" not in out


# ---------------------------------------------------------------------------
# Branching, parent chain, security
# ---------------------------------------------------------------------------


def test_compare_preserves_parent_relationship(two_ledgers: tuple[Path, Path]):
    """Child cards still link to their parent on each side."""
    left, right = two_ledgers
    lL = TrialLedger(left)
    lR = TrialLedger(right)
    lL.append_node(_make_node(1))
    lL.append_node(_make_node(2, parent="n000001"))
    lR.append_node(_make_node(1))
    lR.append_node(_make_node(2, parent="n000001"))
    lR.append_node(_make_node(3, parent="n000001"))  # branch on right side

    out = render_comparison_html(
        left, right,
        left_label="greedy", right_label="bfts-lite",
    )
    # Both sides reference the parent
    assert out.count('href="#n000001"') >= 2
    # Right side has a branch (third node also under n000001)
    assert 'id="n000003"' in out
    _validate(out)


def test_compare_html_escapes_labels(two_ledgers: tuple[Path, Path]):
    left, right = two_ledgers
    lL = TrialLedger(left)
    lR = TrialLedger(right)
    lL.append_node(_make_node(1))
    lR.append_node(_make_node(1))

    nasty_label = "<script>alert(1)</script>"
    out = render_comparison_html(
        left, right,
        left_label=nasty_label,
        right_label="ok",
    )
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_compare_custom_title(two_ledgers: tuple[Path, Path]):
    left, right = two_ledgers
    lL = TrialLedger(left)
    lR = TrialLedger(right)
    lL.append_node(_make_node(1))
    lR.append_node(_make_node(1))

    out = render_comparison_html(
        left, right,
        left_label="A", right_label="B",
        title="My Custom Compare Title",
    )
    assert "<title>My Custom Compare Title</title>" in out
    assert "<h1>My Custom Compare Title</h1>" in out


# ---------------------------------------------------------------------------
# Direction asymmetry: per-side best uses each side's direction
# ---------------------------------------------------------------------------


def test_per_side_direction_picks_correct_best(two_ledgers: tuple[Path, Path]):
    """Left=minimize, right=maximize. Each side picks its own best."""
    left, right = two_ledgers
    lL = TrialLedger(left)
    lR = TrialLedger(right)
    for i in (1, 2):
        lL.append_node(_make_node(i, outcome="keep"))
        lR.append_node(_make_node(i, outcome="keep"))

    out = render_comparison_html(
        left, right,
        left_label="MIN", right_label="MAX",
        left_metric_lookup={"n000001": 5.0, "n000002": 1.0},   # min picks n2
        right_metric_lookup={"n000001": 5.0, "n000002": 1.0},  # max picks n1
        left_direction="minimize", right_direction="maximize",
    )
    # Both sides have best markers; this also implicitly verifies neither
    # crashed when directions disagree.
    assert out.count("★ best") == 2
    # Δ line MUST be omitted because directions differ
    assert "Δ" not in out
