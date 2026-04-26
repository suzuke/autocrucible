"""Tests for `crucible.ledger` — TrialLedger storage layer.

Covers:
  - dataclass roundtrip (AttemptNode / EvalResult / LedgerRecord)
  - TrialLedger append + read semantics
  - state_update merging in all_nodes()
  - Concurrent appends are serialised by fcntl.flock (POSIX only)
  - Partial last line tolerance (torn write mid-line)
  - Schema version mismatch raises explicit error
  - Diff inline limit enforced
"""

from __future__ import annotations

import json
import os
import platform
import threading
import time
from pathlib import Path

import pytest

from crucible.ledger import (
    DIFF_TEXT_INLINE_LIMIT_BYTES,
    LEDGER_SCHEMA_VERSION,
    AttemptNode,
    EvalResult,
    LedgerRecord,
    TrialLedger,
    UnsupportedSchemaVersion,
)


# ---------------------------------------------------------------------------
# Dataclass roundtrip
# ---------------------------------------------------------------------------


def _make_node(seq: int = 0, *, parent_id: str | None = None) -> AttemptNode:
    return AttemptNode(
        id=AttemptNode.short_id(seq),
        parent_id=parent_id,
        commit=f"sha-{seq:08x}",
        backend_kind="litellm",
        model="anthropic/sonnet-4-6",
        prompt_digest=f"prompt-{seq}",
        prompt_ref=f"iter-{seq}/prompt.md",
        diff_text="--- a\n+++ b\n",
        diff_ref=f"iter-{seq}/diff.patch",
        outcome="keep",
        cost_usd=0.04,
        usage_source="api",
        created_at="2026-04-25T12:00:00+00:00",
        worktree_path=f".worktrees/{seq}",
    )


def test_attempt_node_short_id_format():
    assert AttemptNode.short_id(0) == "n000000"
    assert AttemptNode.short_id(42) == "n000042"
    assert AttemptNode.short_id(123456) == "n123456"


def test_attempt_node_roundtrip_via_record():
    node = _make_node(7)
    rec = LedgerRecord.make_node(node)
    raw = rec.to_json()
    restored = LedgerRecord.from_json(raw)
    assert restored.event == "node"
    assert restored.node is not None
    assert restored.node.id == node.id
    assert restored.node.commit == node.commit
    assert restored.node.outcome == node.outcome
    assert restored.node.cost_usd == 0.04


def test_state_update_record_roundtrip():
    rec = LedgerRecord.make_state_update("n000005", "expanded")
    raw = rec.to_json()
    restored = LedgerRecord.from_json(raw)
    assert restored.event == "state_update"
    assert restored.node_id == "n000005"
    assert restored.node_state == "expanded"
    assert restored.node is None


def test_diff_inline_limit_enforced():
    # too-large diff text raises
    with pytest.raises(ValueError, match="diff_text exceeds"):
        AttemptNode(
            id="n000000",
            commit="x",
            diff_text="x" * (DIFF_TEXT_INLINE_LIMIT_BYTES + 1),
        )


def test_schema_version_mismatch_raises():
    bad = json.dumps({"schema_version": 999, "event": "node"})
    with pytest.raises(UnsupportedSchemaVersion):
        LedgerRecord.from_json(bad)


# ---------------------------------------------------------------------------
# TrialLedger basic append / read
# ---------------------------------------------------------------------------


def test_ledger_creates_parent_dir(tmp_path: Path):
    ledger_path = tmp_path / "deep" / "logs" / "run-test" / "ledger.jsonl"
    ledger = TrialLedger(ledger_path)
    ledger.append_node(_make_node(0))
    assert ledger_path.exists()


def test_ledger_append_and_read_one_node(tmp_path: Path):
    ledger = TrialLedger(tmp_path / "ledger.jsonl")
    n = _make_node(0)
    ledger.append_node(n)
    nodes = ledger.all_nodes()
    assert len(nodes) == 1
    assert nodes[0].id == "n000000"


def test_ledger_append_many_preserves_order(tmp_path: Path):
    ledger = TrialLedger(tmp_path / "ledger.jsonl")
    for i in range(5):
        ledger.append_node(_make_node(i))
    nodes = ledger.all_nodes()
    assert [n.id for n in nodes] == [f"n00000{i}" for i in range(5)]


def test_state_update_merges_in_all_nodes(tmp_path: Path):
    ledger = TrialLedger(tmp_path / "ledger.jsonl")
    ledger.append_node(_make_node(0))
    assert ledger.all_nodes()[0].node_state == "frontier"
    ledger.update_state("n000000", "expanded")
    assert ledger.all_nodes()[0].node_state == "expanded"


def test_state_update_for_unknown_node_is_silent(tmp_path: Path):
    """state_update referencing an absent node should not crash; it's a no-op
    on read because the merging logic only updates known node_ids."""
    ledger = TrialLedger(tmp_path / "ledger.jsonl")
    ledger.append_node(_make_node(0))
    ledger.update_state("n999999", "pruned")  # no such node
    nodes = ledger.all_nodes()
    assert len(nodes) == 1
    assert nodes[0].node_state == "frontier"


def test_children_of(tmp_path: Path):
    ledger = TrialLedger(tmp_path / "ledger.jsonl")
    root = _make_node(0)
    ledger.append_node(root)
    ledger.append_node(_make_node(1, parent_id=root.id))
    ledger.append_node(_make_node(2, parent_id=root.id))
    children = ledger.children_of(root.id)
    assert {c.id for c in children} == {"n000001", "n000002"}


def test_frontier_filter(tmp_path: Path):
    ledger = TrialLedger(tmp_path / "ledger.jsonl")
    ledger.append_node(_make_node(0))
    ledger.append_node(_make_node(1))
    ledger.update_state("n000000", "expanded")
    front = ledger.frontier()
    assert {n.id for n in front} == {"n000001"}


def test_best_node_with_metric_lookup(tmp_path: Path):
    ledger = TrialLedger(tmp_path / "ledger.jsonl")
    ledger.append_node(_make_node(0))
    ledger.append_node(_make_node(1))
    ledger.append_node(_make_node(2))
    metrics = {"n000000": 1.0, "n000001": 2.5, "n000002": 1.8}
    best = ledger.best_node(direction="maximize", metric_lookup=metrics)
    assert best is not None and best.id == "n000001"
    best_min = ledger.best_node(direction="minimize", metric_lookup=metrics)
    assert best_min is not None and best_min.id == "n000000"


def test_best_node_no_kept_returns_none(tmp_path: Path):
    ledger = TrialLedger(tmp_path / "ledger.jsonl")
    bad = _make_node(0)
    bad.outcome = "discard"
    ledger.append_node(bad)
    assert ledger.best_node() is None


# ---------------------------------------------------------------------------
# Tolerance: empty/missing/torn-line
# ---------------------------------------------------------------------------


def test_iter_records_on_missing_file(tmp_path: Path):
    ledger = TrialLedger(tmp_path / "no-such.jsonl")
    assert list(ledger.iter_records()) == []


def test_iter_records_skips_empty_lines(tmp_path: Path):
    p = tmp_path / "ledger.jsonl"
    rec = LedgerRecord.make_node(_make_node(0)).to_json()
    p.write_text(rec + "\n\n" + LedgerRecord.make_node(_make_node(1)).to_json() + "\n")
    ledger = TrialLedger(p)
    records = list(ledger.iter_records())
    assert len(records) == 2


def test_torn_last_line_treated_as_eof(tmp_path: Path):
    """Writer crashed mid-line -> last line is partial JSON. Reader must
    silently treat it as EOF rather than raising for the caller."""
    p = tmp_path / "ledger.jsonl"
    rec = LedgerRecord.make_node(_make_node(0)).to_json()
    p.write_text(rec + "\n" + '{"schema_version": 1, "event": "node", "no')  # truncated
    ledger = TrialLedger(p)
    records = list(ledger.iter_records())
    assert len(records) == 1
    assert records[0].node is not None
    assert records[0].node.id == "n000000"


def test_corrupt_middle_line_raises(tmp_path: Path):
    """A bad line in the MIDDLE of the file (not the last) is propagated as
    an error. We can detect this because there is more data after the bad
    line."""
    p = tmp_path / "ledger.jsonl"
    p.write_text(
        LedgerRecord.make_node(_make_node(0)).to_json() + "\n"
        + "this is not valid json\n"
        + LedgerRecord.make_node(_make_node(1)).to_json() + "\n"
    )
    ledger = TrialLedger(p)
    with pytest.raises(json.JSONDecodeError):
        list(ledger.iter_records())


# ---------------------------------------------------------------------------
# Concurrent writes serialised by fcntl.flock
# ---------------------------------------------------------------------------


@pytest.mark.skipif(platform.system() == "Windows",
                    reason="fcntl.flock unavailable on Windows; concurrent "
                           "ledger writes are out of scope for v1.0 there")
def test_concurrent_appends_no_torn_lines(tmp_path: Path):
    """Multiple threads appending in parallel must produce a clean JSONL where
    every line is parseable as a LedgerRecord."""
    ledger = TrialLedger(tmp_path / "ledger.jsonl")
    n_threads = 8
    n_per_thread = 10

    def worker(thread_id: int) -> None:
        for j in range(n_per_thread):
            seq = thread_id * 1000 + j
            ledger.append_node(_make_node(seq))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = list(ledger.iter_records())
    assert len(records) == n_threads * n_per_thread
    # Every line is a valid full node (no torn JSON)
    assert all(r.event == "node" and r.node is not None for r in records)


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-only check")
def test_windows_lock_unsupported(tmp_path: Path):
    ledger = TrialLedger(tmp_path / "ledger.jsonl")
    with pytest.raises(RuntimeError, match="Windows"):
        ledger.append_node(_make_node(0))


# ---------------------------------------------------------------------------
# Sealed EvalResult schema sanity
# ---------------------------------------------------------------------------


def test_eval_result_default_construction():
    e = EvalResult(
        run_id="run1",
        attempt_id="n000000",
        commit="abc",
        eval_command="python evaluate.py",
        eval_manifest_hash="manifest123",
        metric_name="ratio",
        metric_value=1.42,
        metric_direction="maximize",
        diagnostics={"sub_metric": 0.9, "iterations": 5},
        valid=True,
        exit_code=0,
        timed_out=False,
        duration_ms=1234,
        stdout_sha256="abc",
        stderr_sha256="def",
        seal="content-sha256:xyz",
        created_at="2026-04-25T12:00:00+00:00",
    )
    assert e.metric_value == 1.42
    assert e.diagnostics["sub_metric"] == 0.9
    assert e.schema_version == LEDGER_SCHEMA_VERSION


def test_eval_result_metric_value_can_be_none():
    """A crashed evaluation has metric_value=None and valid=False."""
    e = EvalResult(metric_name="x", metric_value=None, valid=False)
    assert e.metric_value is None
    assert not e.valid
