"""Tests for orchestrator → TrialLedger dual-write integration (M1a PR 2).

Focused unit tests on the translation helper `_record_to_attempt_node` and
the dual-log path. Full end-to-end orchestrator tests live in
`test_orchestrator.py`; here we just verify the new ledger plumbing.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crucible.ledger import (
    DIFF_TEXT_INLINE_LIMIT_BYTES,
    AttemptNode,
    TrialLedger,
)
from crucible.results import ExperimentRecord, UsageInfo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def fake_orchestrator(workspace: Path):
    """Build a stand-in object exposing only the methods/attrs that
    `_record_to_attempt_node` and `_dual_log` need.

    We do this rather than instantiate the real Orchestrator because that
    pulls in git/agent/runner setup we don't need to test the dual-log path.
    """
    from crucible.orchestrator import Orchestrator

    # Bind the real methods without running __init__.
    obj = Orchestrator.__new__(Orchestrator)
    obj.workspace = workspace
    obj.tag = "t1"
    obj.agent = SimpleNamespace(model="anthropic/claude-sonnet-4-6")
    obj.results = MagicMock()
    obj.ledger = TrialLedger(workspace / "logs" / "run-t1" / "ledger.jsonl")
    obj._last_attempt_id_by_beam = {}
    return obj


def _record(
    iteration: int = 1,
    status: str = "keep",
    beam_id: int | None = None,
    log_dir: str | None = "logs/iter-1",
    cost: float | None = 0.05,
    diff_text: str | None = "--- a\n+++ b\n",
) -> ExperimentRecord:
    return ExperimentRecord(
        commit="abcd1234",
        metric_value=1.5,
        status=status,
        description="test",
        iteration=iteration,
        timestamp="2026-04-25T12:00:00+00:00",
        diff_text=diff_text,
        usage=UsageInfo(total_cost_usd=cost) if cost is not None else None,
        log_dir=log_dir,
        beam_id=beam_id,
    )


# ---------------------------------------------------------------------------
# _record_to_attempt_node — translation correctness
# ---------------------------------------------------------------------------


def test_translation_basic_fields(fake_orchestrator):
    rec = _record(iteration=3, status="keep")
    agent_result = SimpleNamespace(modified_files=[])
    node = fake_orchestrator._record_to_attempt_node(rec, agent_result, "diff")

    assert node.id == "n000003"
    assert node.commit == "abcd1234"
    assert node.outcome == "keep"
    assert node.model == "anthropic/claude-sonnet-4-6"
    assert node.backend_kind == "claude_sdk"
    assert node.cost_usd == 0.05
    assert node.usage_source == "api"
    assert node.created_at == "2026-04-25T12:00:00+00:00"
    assert node.diff_ref == "logs/iter-1/diff.patch"
    assert node.prompt_ref == "logs/iter-1/prompt.md"


def test_translation_parent_chain_linear(fake_orchestrator):
    """Two consecutive iterations in the same (None) beam produce parent_id chain."""
    agent_result = SimpleNamespace(modified_files=[])
    n1 = fake_orchestrator._record_to_attempt_node(
        _record(iteration=1), agent_result, None,
    )
    n2 = fake_orchestrator._record_to_attempt_node(
        _record(iteration=2), agent_result, None,
    )
    assert n1.parent_id is None
    assert n2.parent_id == "n000001"


def test_translation_parent_chain_per_beam(fake_orchestrator):
    """Beams maintain independent parent chains."""
    agent_result = SimpleNamespace(modified_files=[])
    a1 = fake_orchestrator._record_to_attempt_node(
        _record(iteration=1, beam_id=0), agent_result, None,
    )
    b1 = fake_orchestrator._record_to_attempt_node(
        _record(iteration=1, beam_id=1), agent_result, None,
    )
    a2 = fake_orchestrator._record_to_attempt_node(
        _record(iteration=2, beam_id=0), agent_result, None,
    )
    b2 = fake_orchestrator._record_to_attempt_node(
        _record(iteration=2, beam_id=1), agent_result, None,
    )
    # IDs are namespaced per beam
    assert a1.id == "b0n000001"
    assert b1.id == "b1n000001"
    # Parent chains independent
    assert a1.parent_id is None
    assert b1.parent_id is None
    assert a2.parent_id == "b0n000001"
    assert b2.parent_id == "b1n000001"


def test_translation_diff_truncation(fake_orchestrator):
    """Diff text over the inline limit is truncated and marked."""
    big_diff = "x" * (DIFF_TEXT_INLINE_LIMIT_BYTES + 1000)
    rec = _record(diff_text=big_diff)
    agent_result = SimpleNamespace(modified_files=[])
    node = fake_orchestrator._record_to_attempt_node(rec, agent_result, big_diff)
    assert "[TRUNCATED]" in node.diff_text
    assert len(node.diff_text.encode("utf-8")) <= DIFF_TEXT_INLINE_LIMIT_BYTES


def test_translation_no_cost_marks_unavailable(fake_orchestrator):
    rec = _record(cost=None)
    agent_result = SimpleNamespace(modified_files=[])
    node = fake_orchestrator._record_to_attempt_node(rec, agent_result, "")
    assert node.cost_usd is None
    assert node.usage_source == "unavailable"


def test_translation_missing_log_dir(fake_orchestrator):
    rec = _record(log_dir=None)
    agent_result = SimpleNamespace(modified_files=[])
    node = fake_orchestrator._record_to_attempt_node(rec, agent_result, "")
    assert node.diff_ref == ""
    assert node.prompt_ref == ""


def test_translation_outcome_passthrough(fake_orchestrator):
    """Status strings on ExperimentRecord pass through to AttemptNode.outcome
    unchanged — no second taxonomy."""
    agent_result = SimpleNamespace(modified_files=[])
    for status in ("keep", "discard", "crash", "violation", "skip"):
        rec = _record(status=status)
        # Reset parent chain for each translation
        fake_orchestrator._last_attempt_id_by_beam = {}
        node = fake_orchestrator._record_to_attempt_node(rec, agent_result, "")
        assert node.outcome == status


# ---------------------------------------------------------------------------
# _dual_log — both writes happen, exceptions in ledger don't break results
# ---------------------------------------------------------------------------


def test_dual_log_writes_both(fake_orchestrator, workspace):
    rec = _record(iteration=1, status="keep")
    agent_result = SimpleNamespace(modified_files=[])
    fake_orchestrator._dual_log(rec, agent_result, diff_text="--- a\n+++ b\n")

    # ResultsLog was called
    fake_orchestrator.results.log.assert_called_once_with(rec)

    # Ledger has the record on disk
    ledger_path = workspace / "logs" / "run-t1" / "ledger.jsonl"
    assert ledger_path.exists()
    nodes = fake_orchestrator.ledger.all_nodes()
    assert len(nodes) == 1
    assert nodes[0].id == "n000001"
    assert nodes[0].outcome == "keep"


def test_dual_log_ledger_failure_does_not_break_results(fake_orchestrator):
    """If ledger.append_node raises, _dual_log should swallow the error and
    still have called results.log. The crucible loop must NEVER break because
    of a ledger I/O issue."""
    fake_orchestrator.ledger = MagicMock()
    fake_orchestrator.ledger.append_node.side_effect = OSError("disk full")
    rec = _record(iteration=1, status="keep")
    agent_result = SimpleNamespace(modified_files=[])

    # Should not raise
    fake_orchestrator._dual_log(rec, agent_result, diff_text="")

    fake_orchestrator.results.log.assert_called_once_with(rec)
    fake_orchestrator.ledger.append_node.assert_called_once()


def test_dual_log_records_match_in_full_run(fake_orchestrator, workspace):
    """Five iterations: both logs end up with five entries in matching order."""
    agent_result = SimpleNamespace(modified_files=[])
    statuses = ["crash", "keep", "discard", "keep", "violation"]
    for i, st in enumerate(statuses, start=1):
        fake_orchestrator._dual_log(_record(iteration=i, status=st), agent_result, None)

    # ResultsLog called 5 times (each via its mock)
    assert fake_orchestrator.results.log.call_count == 5

    # Ledger has 5 nodes in order with matching outcomes
    nodes = fake_orchestrator.ledger.all_nodes()
    assert [n.id for n in nodes] == [f"n00000{i}" for i in range(1, 6)]
    assert [n.outcome for n in nodes] == statuses
    # Parent chain is correct
    assert nodes[0].parent_id is None
    for i in range(1, 5):
        assert nodes[i].parent_id == nodes[i - 1].id


# ---------------------------------------------------------------------------
# _ledger_log_no_commit — violation / skip outcomes go straight to ledger
# ---------------------------------------------------------------------------


def _agent_result_with_cost(cost: float | None = 0.002) -> SimpleNamespace:
    return SimpleNamespace(
        modified_files=[],
        usage=UsageInfo(total_cost_usd=cost) if cost is not None else None,
    )


def test_no_commit_violation_writes_ledger(fake_orchestrator):
    """The violation branch records a node with empty commit/diff and the
    violation message in description."""
    fake_orchestrator._iteration = 1
    fake_orchestrator._current_beam_id = None
    fake_orchestrator._ledger_log_no_commit(
        "violation",
        _agent_result_with_cost(),
        "File evaluate.py is read-only",
    )
    nodes = fake_orchestrator.ledger.all_nodes()
    assert len(nodes) == 1
    n = nodes[0]
    assert n.id == "n000001"
    assert n.outcome == "violation"
    assert n.commit == ""
    assert n.diff_text == ""
    assert n.description == "File evaluate.py is read-only"
    assert n.cost_usd == 0.002
    # Did NOT touch ResultsLog
    fake_orchestrator.results.log.assert_not_called()


def test_no_commit_skip_writes_ledger(fake_orchestrator):
    fake_orchestrator._iteration = 2
    fake_orchestrator._current_beam_id = None
    fake_orchestrator._ledger_log_no_commit(
        "skip",
        _agent_result_with_cost(cost=None),
        "agent produced no edits",
    )
    nodes = fake_orchestrator.ledger.all_nodes()
    assert nodes[0].outcome == "skip"
    assert nodes[0].cost_usd is None
    assert nodes[0].usage_source == "unavailable"


def test_no_commit_description_capped_at_500(fake_orchestrator):
    fake_orchestrator._iteration = 1
    fake_orchestrator._current_beam_id = None
    long_msg = "x" * 1000
    fake_orchestrator._ledger_log_no_commit(
        "violation", _agent_result_with_cost(), long_msg,
    )
    nodes = fake_orchestrator.ledger.all_nodes()
    assert len(nodes[0].description) == 500


def test_no_commit_parent_chain_extends(fake_orchestrator):
    """Violation/skip should still extend the parent chain so subsequent
    keep/discard nodes have a parent_id pointing back to the rejection."""
    agent_result = _agent_result_with_cost()
    # iter 1: keep
    fake_orchestrator._dual_log(_record(iteration=1, status="keep"), agent_result, None)
    # iter 2: violation (no commit)
    fake_orchestrator._iteration = 2
    fake_orchestrator._current_beam_id = None
    fake_orchestrator._ledger_log_no_commit("violation", agent_result, "bad edit")
    # iter 3: keep
    fake_orchestrator._dual_log(_record(iteration=3, status="keep"), agent_result, None)

    nodes = fake_orchestrator.ledger.all_nodes()
    assert [n.id for n in nodes] == ["n000001", "n000002", "n000003"]
    assert nodes[0].parent_id is None
    assert nodes[1].parent_id == "n000001"
    assert nodes[2].parent_id == "n000002"


def test_no_commit_ledger_failure_logged_not_raised(fake_orchestrator):
    fake_orchestrator.ledger = MagicMock()
    fake_orchestrator.ledger.append_node.side_effect = OSError("disk full")
    fake_orchestrator._iteration = 1
    fake_orchestrator._current_beam_id = None
    # should not raise
    fake_orchestrator._ledger_log_no_commit("violation", _agent_result_with_cost(), "x")
    fake_orchestrator.ledger.append_node.assert_called_once()


# ---------------------------------------------------------------------------
# Reviewer F1 — resume() must read ledger, not just ResultsLog
# ---------------------------------------------------------------------------


def test_resume_reads_ledger_for_iteration_max(fake_orchestrator):
    """If ledger has more entries than ResultsLog (because violation/skip
    only write ledger), _iteration must be set from the LEDGER max so the
    next iteration doesn't reuse an existing AttemptNode id."""
    # Stub git checkout so we don't need a real repo
    fake_orchestrator.git = MagicMock()
    fake_orchestrator.results.read_all = MagicMock(return_value=[
        SimpleNamespace(iteration=1, status="keep"),
        # Only one ResultsLog entry. Ledger has one more (violation).
    ])
    # Pre-populate ledger with 2 nodes (1 keep + 1 violation)
    fake_orchestrator.ledger.append_node(SimpleNamespace.__class__ and __import__("crucible.ledger", fromlist=["AttemptNode"]).AttemptNode(
        id="n000001", commit="abc", outcome="keep", created_at="2026-04-25T12:00:00+00:00",
    ))
    from crucible.ledger import AttemptNode
    fake_orchestrator.ledger.append_node(AttemptNode(
        id="n000002", parent_id="n000001", commit="", outcome="violation",
        description="bad edit", created_at="2026-04-25T12:01:00+00:00",
    ))

    # Run resume
    from crucible.orchestrator import Orchestrator
    Orchestrator.resume(fake_orchestrator)

    # _iteration must reflect ledger max (2), not ResultsLog len (1)
    assert fake_orchestrator._iteration == 2
    # parent chain rebuilt
    assert fake_orchestrator._last_attempt_id_by_beam[None] == "n000002"


def test_resume_handles_empty_state(fake_orchestrator):
    """resume() on empty workspace should set _iteration=0 and empty parent map."""
    fake_orchestrator.git = MagicMock()
    fake_orchestrator.results.read_all = MagicMock(return_value=[])
    from crucible.orchestrator import Orchestrator
    Orchestrator.resume(fake_orchestrator)
    assert fake_orchestrator._iteration == 0
    assert fake_orchestrator._last_attempt_id_by_beam == {}


def test_resume_reconstructs_beam_chain(fake_orchestrator):
    """Per-beam parent chains must be rebuilt from b<beam>n<seq> ids."""
    fake_orchestrator.git = MagicMock()
    fake_orchestrator.results.read_all = MagicMock(return_value=[])
    from crucible.ledger import AttemptNode
    # beam 0: n000001 → n000002
    fake_orchestrator.ledger.append_node(AttemptNode(id="b0n000001", outcome="keep"))
    fake_orchestrator.ledger.append_node(AttemptNode(id="b0n000002", parent_id="b0n000001", outcome="discard"))
    # beam 1: n000001 only
    fake_orchestrator.ledger.append_node(AttemptNode(id="b1n000001", outcome="keep"))

    from crucible.orchestrator import Orchestrator
    Orchestrator.resume(fake_orchestrator)
    assert fake_orchestrator._last_attempt_id_by_beam[0] == "b0n000002"
    assert fake_orchestrator._last_attempt_id_by_beam[1] == "b1n000001"
    # Iteration is max sequence across all ids
    assert fake_orchestrator._iteration == 2


# ---------------------------------------------------------------------------
# M1b PR 3 — BranchFrom commit lookup + strategy context build
# ---------------------------------------------------------------------------


def test_lookup_commit_for_node_returns_committed_sha(fake_orchestrator):
    from crucible.ledger import AttemptNode
    fake_orchestrator.ledger.append_node(
        AttemptNode(id="n000001", commit="abc1234", outcome="keep")
    )
    fake_orchestrator.ledger.append_node(
        AttemptNode(id="n000002", parent_id="n000001",
                    commit="def5678", outcome="keep")
    )
    from crucible.orchestrator import Orchestrator
    sha = Orchestrator._lookup_commit_for_node(fake_orchestrator, "n000002")
    assert sha == "def5678"


def test_lookup_commit_for_violation_node_returns_none(fake_orchestrator):
    """Violation/skip nodes have empty commit (no commit happened) → None."""
    from crucible.ledger import AttemptNode
    fake_orchestrator.ledger.append_node(AttemptNode(
        id="n000001", commit="", outcome="violation",
        description="bad edit",
    ))
    from crucible.orchestrator import Orchestrator
    sha = Orchestrator._lookup_commit_for_node(fake_orchestrator, "n000001")
    assert sha is None


def test_lookup_commit_for_unknown_node_returns_none(fake_orchestrator):
    from crucible.orchestrator import Orchestrator
    sha = Orchestrator._lookup_commit_for_node(fake_orchestrator, "n999999")
    assert sha is None


def test_build_strategy_context_populates_metric_lookup(fake_orchestrator):
    """_build_strategy_context maps ResultsLog records to the AttemptNode
    id schema so BFTSLiteStrategy can pick the best kept node."""
    from crucible.ledger import AttemptNode
    from crucible.results import ExperimentRecord
    fake_orchestrator.ledger.append_node(AttemptNode(id="n000001", outcome="keep"))
    fake_orchestrator.ledger.append_node(AttemptNode(id="n000002", outcome="discard"))
    fake_orchestrator.results.read_all = MagicMock(return_value=[
        ExperimentRecord(commit="x", metric_value=1.5, status="keep",
                          description="", iteration=1),
        ExperimentRecord(commit="y", metric_value=0.8, status="discard",
                          description="", iteration=2),
    ])
    fake_orchestrator._iteration = 0
    fake_orchestrator._baseline_commit = "abc"

    cfg = SimpleNamespace(metric=SimpleNamespace(direction="maximize"))
    fake_orchestrator.config = cfg
    fake_orchestrator._count_plateau_streak = MagicMock(return_value=2)

    from crucible.orchestrator import Orchestrator
    ctx = Orchestrator._build_strategy_context(
        fake_orchestrator,
        session_count=2,
        plateau_threshold=8,
        max_iterations=10,
    )
    assert ctx.metric_lookup == {"n000001": 1.5, "n000002": 0.8}
    assert ctx.metric_direction == "maximize"
    assert ctx.iteration_count == 2
    assert ctx.plateau_streak == 2
    assert ctx.plateau_threshold == 8
    assert ctx.baseline_commit == "abc"


# ---------------------------------------------------------------------------
# M1b PR 6 — sealed EvalResult artefact write
# ---------------------------------------------------------------------------


def test_write_eval_result_artifact_creates_json_with_seal(fake_orchestrator, workspace):
    """Per spec §4 / §11: the host process is the SOLE writer of
    eval-result.json. Verify the file appears at the spec path with
    a content-sha256 seal and that the returned hash matches the disk."""
    import hashlib
    import json as _json
    from types import SimpleNamespace

    fake_orchestrator.config = SimpleNamespace(
        commands=SimpleNamespace(eval="cat run.log", run="python evaluate.py"),
        metric=SimpleNamespace(name="ratio", direction="maximize"),
    )
    fake_orchestrator._current_beam_id = None
    run_result = SimpleNamespace(
        stdout="metric: 1.42\n", stderr_tail="", exit_code=0, timed_out=False,
    )

    from crucible.orchestrator import Orchestrator
    rel_path, sha = Orchestrator._write_eval_result_artifact(
        fake_orchestrator,
        iteration=3,
        commit_hash="abc1234",
        run_result=run_result,
        metric_value=1.42,
        run_duration_seconds=0.5,
    )

    assert rel_path == "logs/run-t1/iter-3/eval-result.json"
    assert sha is not None
    target = workspace / rel_path
    assert target.exists()
    payload = _json.loads(target.read_text())
    assert payload["metric_value"] == 1.42
    assert payload["metric_name"] == "ratio"
    assert payload["seal"].startswith("content-sha256:")
    assert payload["valid"] is True
    assert payload["exit_code"] == 0
    # Returned sha matches disk
    assert sha == hashlib.sha256(target.read_bytes()).hexdigest()


def test_write_eval_result_artifact_handles_missing_metric(fake_orchestrator, workspace):
    """metric_value=None → valid=False, seal still computed."""
    import json as _json
    from types import SimpleNamespace

    fake_orchestrator.config = SimpleNamespace(
        commands=SimpleNamespace(eval="cat run.log", run="python evaluate.py"),
        metric=SimpleNamespace(name="ratio", direction="maximize"),
    )
    fake_orchestrator._current_beam_id = None
    run_result = SimpleNamespace(
        stdout="", stderr_tail="boom", exit_code=1, timed_out=False,
    )

    from crucible.orchestrator import Orchestrator
    rel_path, sha = Orchestrator._write_eval_result_artifact(
        fake_orchestrator,
        iteration=1,
        commit_hash="",
        run_result=run_result,
        metric_value=None,
        run_duration_seconds=0.1,
    )

    target = workspace / rel_path
    payload = _json.loads(target.read_text())
    assert payload["metric_value"] is None
    assert payload["valid"] is False
    assert payload["exit_code"] == 1


def test_write_eval_result_artifact_handles_io_failure(fake_orchestrator):
    """If the write fails, returns (None, None) and does not raise."""
    from types import SimpleNamespace
    fake_orchestrator.config = SimpleNamespace(
        commands=SimpleNamespace(eval="cat run.log", run="python evaluate.py"),
        metric=SimpleNamespace(name="ratio", direction="maximize"),
    )
    fake_orchestrator._current_beam_id = None
    fake_orchestrator.workspace = Path("/no/such/dir/that/exists")
    run_result = SimpleNamespace(stdout="", stderr_tail="", exit_code=0, timed_out=False)

    from crucible.orchestrator import Orchestrator
    rel_path, sha = Orchestrator._write_eval_result_artifact(
        fake_orchestrator,
        iteration=1,
        commit_hash="abc",
        run_result=run_result,
        metric_value=1.0,
        run_duration_seconds=0.1,
    )
    assert rel_path is None
    assert sha is None
