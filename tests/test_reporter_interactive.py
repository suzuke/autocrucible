"""Tests for `crucible.reporter.interactive.render_interactive_html` —
M3 PR 15.

Reviewer round 1 verdict: NEEDS_TWEAK with 4 gaps. Tests below cover:
  - d3 vendor presence + version pin + license attribution in output
  - `</script>` XSS regression (description containing closing-tag)
  - Default-collapse beyond depth 2 (UI affordance check)
  - Tree topology preservation (deserialise + assert all node IDs)
  - Orphan node styling
  - Static reporter byte-equivalence (untouched)
  - Empty ledger graceful "(no attempts)" message
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from crucible.ledger import AttemptNode, TrialLedger
from crucible.reporter import (
    render_interactive_html,
    render_static_html,
)
from crucible.reporter._vendor import D3_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    seq: int,
    *,
    parent: str | None = None,
    outcome: str = "keep",
    description: str | None = None,
) -> AttemptNode:
    return AttemptNode(
        id=AttemptNode.short_id(seq),
        parent_id=parent,
        commit=f"sha-{seq:08x}",
        backend_kind="claude_sdk",
        model="anthropic/sonnet-4-6",
        outcome=outcome,
        cost_usd=0.001 * seq,
        created_at="2026-04-25T12:00:00+00:00",
        description=description,
    )


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger.jsonl"


def _extract_tree_payload(out: str) -> dict | None:
    """Pull the embedded JSON payload out of the rendered HTML.

    The interactive renderer puts it inside a <script type="application/json">
    block with id="tree-payload". We re-parse that block and reverse the
    `_safe_json_for_script` escapes (the only one we apply to actual JSON
    bytes is `</` → `<\\/`)."""
    m = re.search(
        r'<script id="tree-payload" type="application/json">\s*(.*?)\s*</script>',
        out,
        re.DOTALL,
    )
    if not m:
        return None
    raw = m.group(1).strip()
    if raw == "null":
        return None
    # Reverse the safe-script escapes
    raw = raw.replace("<\\/", "</")
    return json.loads(raw)


def _walk_node_ids(payload: dict) -> set[str]:
    """Collect every `id` field in the embedded tree (excluding synthetic root)."""
    ids: set[str] = set()
    def walk(node: dict) -> None:
        if not node.get("synthetic_root"):
            nid = node.get("id")
            if nid:
                ids.add(nid)
        for c in node.get("children", []):
            walk(c)
    walk(payload)
    return ids


# ---------------------------------------------------------------------------
# Empty ledger / smoke
# ---------------------------------------------------------------------------


def test_empty_ledger_renders_empty_state(ledger_path: Path):
    ledger_path.touch()
    out = render_interactive_html(ledger_path)
    assert "<html" in out
    assert "No attempts recorded" in out
    # Empty payload renders as `null` so the JS no-ops.
    assert _extract_tree_payload(out) is None


def test_missing_ledger_file_does_not_crash(tmp_path: Path):
    out = render_interactive_html(tmp_path / "nope.jsonl")
    assert "No attempts recorded" in out


def test_single_node_renders_with_d3_payload(ledger_path: Path):
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, outcome="keep"))
    out = render_interactive_html(ledger_path)
    payload = _extract_tree_payload(out)
    assert payload is not None
    assert payload["synthetic_root"] is True
    assert _walk_node_ids(payload) == {"n000001"}


# ---------------------------------------------------------------------------
# Reviewer gap #1: d3 vendor presence + attribution
# ---------------------------------------------------------------------------


def test_d3_source_inlined_with_version(ledger_path: Path):
    """Vendored d3 source MUST be embedded; CDN links would break the
    'self-contained, opens offline' M1a contract."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))
    out = render_interactive_html(ledger_path)
    # d3.js v7 minified header preserved
    assert "d3js.org v7" in out
    assert "Mike Bostock" in out


def test_d3_version_displayed_in_attribution(ledger_path: Path):
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))
    out = render_interactive_html(ledger_path)
    # Version pin shown to user in attribution footer
    assert f"v{D3_VERSION}" in out


def test_d3_license_attribution_in_html_comment(ledger_path: Path):
    """ISC license / copyright must be visible in the HTML comment so
    end-users can identify the embedded library and attribution chain."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))
    out = render_interactive_html(ledger_path)
    # Look for the "Vendored d3.js" comment with license + version
    assert "Vendored d3.js" in out
    assert "ISC License" in out
    assert "Copyright 2010-2023 Mike Bostock" in out


def test_no_external_url_dependencies(ledger_path: Path):
    """No `src=` references to external CDNs that would break offline."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))
    out = render_interactive_html(ledger_path)
    # No `<script src="...d3..."` patterns — d3 is inlined
    assert not re.search(
        r'<script[^>]*\ssrc="[^"]*d3[^"]*"', out, re.IGNORECASE
    )


# ---------------------------------------------------------------------------
# Reviewer gap #2: </script> XSS escape (CRITICAL)
# ---------------------------------------------------------------------------


def test_script_close_tag_in_description_does_not_break_out(ledger_path: Path):
    """A node `description` containing `</script><img onerror=...>` must
    NOT break out of the embedded JSON `<script>` block. Standard fix:
    escape `</` → `<\\/` in JSON before injection. Reviewer round 1 gap #2."""
    payload_attack = "</script><img src=x onerror=alert('xss')>"
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, description=payload_attack))
    out = render_interactive_html(ledger_path)

    # The literal `</script>` from the attack payload must NOT appear
    # verbatim outside the closing tag of our own scripts.
    # Count actual closing-script tags vs. injected ones.
    # The renderer emits exactly two `<script>` blocks (d3 source +
    # tree payload + 1 inline JS), so there should be at most 3
    # `</script>` closures it owns.
    script_close_count = out.count("</script>")
    # Plus 1 for the inline JS at the end:
    assert script_close_count == 3, (
        f"Expected exactly 3 own </script> closings, got {script_close_count}; "
        f"description payload may have broken out. snippet around match: "
        f"{out[max(0, out.find('onerror'))-60:out.find('onerror')+80]!r}"
    )

    # Verify the escaped form `<\/script>` appears in the JSON payload
    # (this is what prevents the breakout).
    assert "<\\/script>" in out

    # Round-trip: payload still deserializes cleanly with the literal
    # description string preserved.
    payload = _extract_tree_payload(out)
    assert payload is not None
    nodes_with_attack = []
    def walk(n):
        if not n.get("synthetic_root"):
            nodes_with_attack.append(n)
        for c in n.get("children", []):
            walk(c)
    walk(payload)
    assert len(nodes_with_attack) == 1
    assert nodes_with_attack[0]["description"] == payload_attack


def test_unicode_line_separator_escaped(ledger_path: Path):
    """U+2028 LINE SEPARATOR is treated as a line terminator inside JS
    strings on some engines, breaking the embedded JSON. Must be
    escaped as \\u2028 in the script payload."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, description="line1 line2"))
    out = render_interactive_html(ledger_path)
    # The raw U+2028 char must NOT be present in the output (escaped form)
    assert " " not in out
    assert "\\u2028" in out


# ---------------------------------------------------------------------------
# Reviewer gap #3: default-collapse beyond depth 2
# ---------------------------------------------------------------------------


def test_collapse_button_present_in_toolbar(ledger_path: Path):
    """The 'Collapse to depth 2' / 'Expand all' affordances must exist."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))
    out = render_interactive_html(ledger_path)
    assert 'id="btn-expand-all"' in out
    assert 'id="btn-collapse-all"' in out
    assert "Expand all" in out
    assert "Collapse to depth 2" in out


def test_default_collapse_logic_in_embedded_js(ledger_path: Path):
    """Embedded JS should contain the depth-2 collapse-on-load logic.

    Reviewer round 1 gap #3: doom-loop runs produce 100+ nodes; full-
    expand on load is unusable. Verifying structurally — the JS must
    contain a `d.depth >= 2` collapse pattern."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1))
    out = render_interactive_html(ledger_path)
    assert "d.depth >= 2" in out


# ---------------------------------------------------------------------------
# Reviewer gap #4: test coverage — orphan styling, JSON deserialization
# ---------------------------------------------------------------------------


def test_orphan_node_marked_in_payload(ledger_path: Path):
    """A node whose parent_id references a non-existent node is an
    orphan. Must be marked `is_orphan: true` in the embedded payload
    so the d3 script can apply the orphan styling (dashed circle)."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, outcome="keep"))
    # n000002 has parent_id pointing to a node that doesn't exist
    ledger.append_node(_make_node(2, parent="n999999", outcome="keep"))
    out = render_interactive_html(ledger_path)
    payload = _extract_tree_payload(out)
    assert payload is not None
    found_orphan = False
    def walk(n):
        nonlocal found_orphan
        if n.get("id") == "n000002" and n.get("is_orphan"):
            found_orphan = True
        for c in n.get("children", []):
            walk(c)
    walk(payload)
    assert found_orphan
    # CSS class for orphan styling exists
    assert "is-orphan" in out


def test_payload_deserializes_to_same_node_ids(ledger_path: Path):
    """Catches silent JSON serialization regressions (a node dropped on
    the JS side wouldn't be caught by HTML structure tests). Reviewer
    round 1 gap #4."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, outcome="keep"))
    ledger.append_node(_make_node(2, parent="n000001", outcome="keep"))
    ledger.append_node(_make_node(3, parent="n000002", outcome="discard"))
    ledger.append_node(_make_node(4, parent="n000001", outcome="keep"))  # branch

    out = render_interactive_html(ledger_path)
    payload = _extract_tree_payload(out)
    assert payload is not None
    embedded_ids = _walk_node_ids(payload)

    ledger_ids = {n.id for n in ledger.all_nodes()}
    assert embedded_ids == ledger_ids


def test_branching_topology_preserved(ledger_path: Path):
    """Two children of the same parent must both appear under that parent
    in the embedded tree (BFTS branch case)."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, outcome="keep"))
    ledger.append_node(_make_node(2, parent="n000001", outcome="discard"))
    ledger.append_node(_make_node(3, parent="n000001", outcome="keep"))

    out = render_interactive_html(ledger_path)
    payload = _extract_tree_payload(out)

    # Find n000001 and assert it has 2 children
    found = []
    def walk(n):
        if n.get("id") == "n000001":
            found.append(n)
        for c in n.get("children", []):
            walk(c)
    walk(payload)
    assert len(found) == 1
    assert len(found[0]["children"]) == 2
    child_ids = {c["id"] for c in found[0]["children"]}
    assert child_ids == {"n000002", "n000003"}


def test_best_marker_in_payload(ledger_path: Path):
    ledger = TrialLedger(ledger_path)
    for i in (1, 2, 3):
        ledger.append_node(_make_node(i, outcome="keep"))
    metrics = {"n000001": 0.5, "n000002": 1.7, "n000003": 1.0}
    out = render_interactive_html(
        ledger_path, metric_lookup=metrics, metric_direction="maximize"
    )
    payload = _extract_tree_payload(out)
    found_best = []
    def walk(n):
        if n.get("is_best"):
            found_best.append(n)
        for c in n.get("children", []):
            walk(c)
    walk(payload)
    assert len(found_best) == 1
    assert found_best[0]["id"] == "n000002"


def test_outcome_colors_in_payload(ledger_path: Path):
    """Each node carries its outcome's color hex pair so d3 can fill /
    stroke without re-deriving from outcome strings."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, outcome="keep"))
    ledger.append_node(_make_node(2, parent="n000001", outcome="discard"))
    out = render_interactive_html(ledger_path)
    payload = _extract_tree_payload(out)
    nodes_seen = []
    def walk(n):
        if not n.get("synthetic_root"):
            nodes_seen.append(n)
        for c in n.get("children", []):
            walk(c)
    walk(payload)
    by_id = {n["id"]: n for n in nodes_seen}
    # Both have non-default colors set
    assert by_id["n000001"]["outcome_fg"] != ""
    assert by_id["n000001"]["outcome_bg"] != ""
    # keep ≠ discard color
    assert by_id["n000001"]["outcome_fg"] != by_id["n000002"]["outcome_fg"]


# ---------------------------------------------------------------------------
# Static reporter byte-equivalence (reviewer tripwire)
# ---------------------------------------------------------------------------


def test_static_reporter_unchanged_smoke(ledger_path: Path):
    """Reviewer tripwire: PR 15 must NOT change static reporter output.
    This is a smoke equivalent — full byte-equivalence is implicitly
    asserted by the existing test_reporter_html.py suite (unchanged)."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, outcome="keep"))
    static_out = render_static_html(ledger_path)
    # Static reporter does NOT contain d3 / interactive markers
    assert "d3.js" not in static_out
    assert "tree-payload" not in static_out
    assert "btn-expand-all" not in static_out


def test_compare_module_untouched_signature_check():
    """Reviewer tripwire: compare.py is out of scope for PR 15.
    Importing render_comparison_html must not error and signature
    must match M2 PR 11 contract (no new args added by accident)."""
    from crucible.reporter import render_comparison_html
    import inspect
    sig = inspect.signature(render_comparison_html)
    # Required positional: left_ledger_path, right_ledger_path
    params = list(sig.parameters.keys())
    assert params[0] == "left_ledger_path"
    assert params[1] == "right_ledger_path"
    # Known kwargs from M2 PR 11
    expected_kwargs = {
        "left_label", "right_label", "title",
        "left_metric_lookup", "right_metric_lookup",
        "left_direction", "right_direction",
    }
    actual_kwargs = set(sig.parameters.keys()) - {"left_ledger_path", "right_ledger_path"}
    assert actual_kwargs == expected_kwargs, (
        f"compare.py signature changed; PR 15 should not touch it. "
        f"Diff: {actual_kwargs ^ expected_kwargs}"
    )


def test_unicode_paragraph_separator_escaped(ledger_path: Path):
    """U+2029 PARAGRAPH SEPARATOR has the same JS-line-terminator hazard
    as U+2028. Must be escaped to `\\u2029`. Reviewer round 2 polish."""
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(1, description="para1 para2"))
    out = render_interactive_html(ledger_path)
    assert " " not in out
    assert "\\u2029" in out


def test_d3_integrity_sha256_pinned():
    """Reviewer round 2 polish: the vendored d3 file SHA-256 is pinned.
    If anyone in-place edits `d3.v7.9.0.min.js` without bumping the
    version, the integrity check fails."""
    from crucible.reporter._vendor import D3_SHA256, verify_d3_integrity
    assert isinstance(D3_SHA256, str) and len(D3_SHA256) == 64
    assert verify_d3_integrity() is True
