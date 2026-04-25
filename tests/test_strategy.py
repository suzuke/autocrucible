"""Tests for `crucible.strategy` — SearchStrategy Protocol + reference impls.

This is M1b PR 1. The Protocol is purely additive — no existing orchestrator
code is rewired yet. These tests verify the contract holds and the three
reference implementations (greedy / restart / bfts-lite) decide correctly
on a variety of ledger states.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Sequence

import pytest

from crucible.ledger import AttemptNode
from crucible.strategy import (
    BFTSLiteStrategy,
    BranchFrom,
    Continue,
    GreedyStrategy,
    Restart,
    RestartStrategy,
    SearchStrategy,
    Stop,
    StrategyAction,
    StrategyContext,
    make_strategy,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _node(seq: int, *, parent: str | None = None, outcome: str = "keep") -> AttemptNode:
    return AttemptNode(
        id=AttemptNode.short_id(seq),
        parent_id=parent,
        commit=f"sha-{seq:08x}",
        outcome=outcome,
        created_at="2026-04-25T12:00:00+00:00",
    )


def _ctx(
    *,
    nodes: Sequence[AttemptNode] = (),
    metrics: dict[str, float] | None = None,
    direction: str = "maximize",
    iters: int = 0,
    streak: int = 0,
    plateau: int = 8,
    max_iters: int | None = None,
    baseline: str | None = "abc1234",
) -> StrategyContext:
    return StrategyContext(
        ledger_nodes=list(nodes),
        metric_lookup=metrics or {},
        metric_direction=direction,  # type: ignore[arg-type]
        iteration_count=iters,
        plateau_streak=streak,
        plateau_threshold=plateau,
        max_iterations=max_iters,
        baseline_commit=baseline,
    )


# ---------------------------------------------------------------------------
# Protocol contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strat_cls", [GreedyStrategy, RestartStrategy, BFTSLiteStrategy])
def test_reference_strategies_implement_protocol(strat_cls):
    s = strat_cls()
    assert isinstance(s, SearchStrategy)
    assert s.name in ("greedy", "restart", "bfts-lite")


def test_make_strategy_factory_dispatches():
    assert isinstance(make_strategy("greedy"), GreedyStrategy)
    assert isinstance(make_strategy("restart"), RestartStrategy)
    assert isinstance(make_strategy("bfts-lite"), BFTSLiteStrategy)


def test_make_strategy_unknown_raises():
    with pytest.raises(ValueError, match="unknown search strategy"):
        make_strategy("ant-colony")


# ---------------------------------------------------------------------------
# GreedyStrategy
# ---------------------------------------------------------------------------


def test_greedy_continues_when_no_failures():
    s = GreedyStrategy()
    assert isinstance(s.decide(_ctx(iters=3, streak=0, plateau=8)), Continue)


def test_greedy_stops_at_plateau():
    s = GreedyStrategy()
    action = s.decide(_ctx(iters=10, streak=8, plateau=8))
    assert isinstance(action, Stop)
    assert "plateau" in action.reason.lower()


def test_greedy_stops_at_max_iterations():
    s = GreedyStrategy()
    action = s.decide(_ctx(iters=20, max_iters=20))
    assert isinstance(action, Stop)
    assert "max_iterations" in action.reason


def test_greedy_max_iters_takes_priority_over_plateau():
    """Max iterations is a harder stop than plateau."""
    s = GreedyStrategy()
    action = s.decide(_ctx(iters=20, streak=8, plateau=8, max_iters=20))
    assert isinstance(action, Stop)
    # Either reason is acceptable; just verify it stops.


def test_greedy_no_pruning():
    s = GreedyStrategy()
    assert s.should_prune(_ctx(), "n000001") is False


# ---------------------------------------------------------------------------
# RestartStrategy
# ---------------------------------------------------------------------------


def test_restart_continues_when_no_failures():
    s = RestartStrategy()
    assert isinstance(s.decide(_ctx(iters=2)), Continue)


def test_restart_returns_restart_at_plateau():
    s = RestartStrategy()
    action = s.decide(_ctx(iters=10, streak=8, plateau=8))
    assert isinstance(action, Restart)


def test_restart_stops_at_max_iterations_before_restarting():
    s = RestartStrategy()
    action = s.decide(_ctx(iters=20, streak=8, plateau=8, max_iters=20))
    assert isinstance(action, Stop)


# ---------------------------------------------------------------------------
# BFTSLiteStrategy — the new behavior
# ---------------------------------------------------------------------------


def test_bfts_continues_when_no_kept_nodes():
    s = BFTSLiteStrategy()
    nodes = [_node(1, outcome="discard")]
    assert isinstance(s.decide(_ctx(nodes=nodes)), Continue)


def test_bfts_continues_when_only_one_kept_node():
    """Single kept node + most-recent IS that node → just continue extending."""
    s = BFTSLiteStrategy()
    n1 = _node(1, outcome="keep")
    nodes = [n1]
    metrics = {"n000001": 1.0}
    action = s.decide(_ctx(nodes=nodes, metrics=metrics))
    assert isinstance(action, Continue)


def test_bfts_branches_to_best_when_current_is_not_best():
    """Most recent node is a child of n000002, but best is n000001
    (higher metric). Strategy should BranchFrom(n000001)."""
    s = BFTSLiteStrategy()
    n1 = _node(1, outcome="keep")
    n2 = _node(2, parent="n000001", outcome="keep")
    n3 = _node(3, parent="n000002", outcome="discard")  # discard doesn't reset frontier
    nodes = [n1, n2, n3]
    metrics = {"n000001": 5.0, "n000002": 1.0}
    action = s.decide(_ctx(nodes=nodes, metrics=metrics, direction="maximize"))
    assert isinstance(action, BranchFrom)
    assert action.parent_id == "n000001"


def test_bfts_minimize_picks_smallest_metric():
    """For minimize objectives (TSP-like), branch from smallest kept metric.
    Set up so the most-recent node is NOT a child of the best, otherwise
    the strategy returns Continue. n3 extends from n000001 (the worse
    metric), so BFTS should redirect to n000002 (the better one)."""
    s = BFTSLiteStrategy()
    n1 = _node(1, outcome="keep")
    n2 = _node(2, parent="n000001", outcome="keep")
    n3 = _node(3, parent="n000001", outcome="discard")  # extending wrong branch
    nodes = [n1, n2, n3]
    metrics = {"n000001": 5.0, "n000002": 1.0}
    action = s.decide(_ctx(nodes=nodes, metrics=metrics, direction="minimize"))
    assert isinstance(action, BranchFrom)
    assert action.parent_id == "n000002"  # smaller metric


def test_bfts_continues_when_already_extending_best():
    """If most recent node's parent IS the best, no need to branch — just
    continue extending."""
    s = BFTSLiteStrategy()
    n1 = _node(1, outcome="keep")
    n2 = _node(2, parent="n000001", outcome="keep")
    nodes = [n1, n2]
    metrics = {"n000001": 5.0, "n000002": 7.0}
    # n000002 is best (7.0 > 5.0), and most-recent IS n000002 → continue
    action = s.decide(_ctx(nodes=nodes, metrics=metrics, direction="maximize"))
    assert isinstance(action, Continue)


def test_bfts_stops_at_max_iterations():
    s = BFTSLiteStrategy()
    action = s.decide(_ctx(iters=30, max_iters=30))
    assert isinstance(action, Stop)


def test_bfts_should_prune_no_children():
    """A candidate with zero direct children cannot be pruned."""
    s = BFTSLiteStrategy()
    nodes = [_node(1, outcome="keep")]
    metrics = {"n000001": 1.0}
    assert s.should_prune(_ctx(nodes=nodes, metrics=metrics), "n000001") is False


# ---------------------------------------------------------------------------
# BFTSLiteStrategy.should_prune — doom-loop pruning (M2 PR 10)
# ---------------------------------------------------------------------------


def test_prune_trailing_failures_at_threshold():
    """3 trailing discards → candidate is pruned."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),
        _node(2, parent="n000001", outcome="discard"),
        _node(3, parent="n000001", outcome="discard"),
        _node(4, parent="n000001", outcome="discard"),
    ]
    metrics = {"n000001": 1.0}
    assert s.should_prune(_ctx(nodes=nodes, metrics=metrics), "n000001") is True


def test_prune_below_threshold_does_not_fire():
    """Only 2 trailing discards (below threshold of 3) → not pruned."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),
        _node(2, parent="n000001", outcome="discard"),
        _node(3, parent="n000001", outcome="discard"),
    ]
    metrics = {"n000001": 1.0}
    assert s.should_prune(_ctx(nodes=nodes, metrics=metrics), "n000001") is False


def test_prune_strict_improvement_resets_streak():
    """A kept child with strictly better metric breaks the trailing failure streak."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),
        _node(2, parent="n000001", outcome="discard"),
        _node(3, parent="n000001", outcome="discard"),
        _node(4, parent="n000001", outcome="keep"),  # strict improvement → reset
        _node(5, parent="n000001", outcome="discard"),
    ]
    metrics = {"n000001": 1.0, "n000004": 2.0}
    assert s.should_prune(_ctx(nodes=nodes, metrics=metrics), "n000001") is False


def test_prune_equal_metric_kept_counts_as_failure():
    """A kept child with metric == candidate's is NOT a strict improvement."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),
        _node(2, parent="n000001", outcome="keep"),       # equal — not improvement
        _node(3, parent="n000001", outcome="keep"),       # equal — not improvement
        _node(4, parent="n000001", outcome="keep"),       # equal — not improvement
    ]
    metrics = {"n000001": 1.0, "n000002": 1.0, "n000003": 1.0, "n000004": 1.0}
    assert s.should_prune(_ctx(nodes=nodes, metrics=metrics), "n000001") is True


def test_prune_discard_with_worse_metric_counts():
    """Discard outcomes count regardless of metric."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),
        _node(2, parent="n000001", outcome="discard"),
        _node(3, parent="n000001", outcome="discard"),
        _node(4, parent="n000001", outcome="discard"),
    ]
    metrics = {"n000001": 5.0, "n000002": 0.5, "n000003": 0.6, "n000004": 0.4}
    assert s.should_prune(_ctx(nodes=nodes, metrics=metrics), "n000001") is True


def test_prune_crash_counts_as_failed_expansion():
    """Crash / violation / skip outcomes (no metric) count toward the streak."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),
        _node(2, parent="n000001", outcome="crash"),
        _node(3, parent="n000001", outcome="violation"),
        _node(4, parent="n000001", outcome="skip"),
    ]
    metrics = {"n000001": 1.0}  # no metrics for crash/violation/skip
    assert s.should_prune(_ctx(nodes=nodes, metrics=metrics), "n000001") is True


def test_prune_unrelated_siblings_dont_affect_candidate():
    """Children of OTHER parents must not enter candidate's streak."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),
        _node(2, outcome="keep"),  # different root, different parent
        _node(3, parent="n000002", outcome="discard"),
        _node(4, parent="n000002", outcome="discard"),
        _node(5, parent="n000002", outcome="discard"),
        _node(6, parent="n000001", outcome="discard"),  # only ONE child of n000001
    ]
    metrics = {"n000001": 1.0, "n000002": 1.0}
    assert s.should_prune(_ctx(nodes=nodes, metrics=metrics), "n000001") is False


def test_prune_candidate_without_metric_returns_false():
    """Defensive: bootstrap nodes without a metric cannot be reasoned about."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),
        _node(2, parent="n000001", outcome="discard"),
        _node(3, parent="n000001", outcome="discard"),
        _node(4, parent="n000001", outcome="discard"),
    ]
    metrics: dict[str, float] = {}  # no metric for n000001
    assert s.should_prune(_ctx(nodes=nodes, metrics=metrics), "n000001") is False


def test_prune_minimize_direction_strict_improvement():
    """Under minimize, lower metric is strict improvement → resets streak."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),
        _node(2, parent="n000001", outcome="discard"),
        _node(3, parent="n000001", outcome="discard"),
        _node(4, parent="n000001", outcome="keep"),
    ]
    metrics = {"n000001": 5.0, "n000004": 3.0}
    assert s.should_prune(
        _ctx(nodes=nodes, metrics=metrics, direction="minimize"), "n000001"
    ) is False


# ---------------------------------------------------------------------------
# BFTSLiteStrategy.decide — pruning interaction with selection
# ---------------------------------------------------------------------------


def test_decide_filters_pruned_picks_next_best():
    """Pruned high-score branch starves; decide() picks next-best unpruned."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),  # score 5.0 — will be pruned
        _node(2, outcome="keep"),  # score 3.0 — viable
        _node(3, parent="n000001", outcome="discard"),
        _node(4, parent="n000001", outcome="discard"),
        _node(5, parent="n000001", outcome="discard"),
        _node(6, parent="n000002", outcome="keep"),  # most-recent, child of n2
    ]
    metrics = {"n000001": 5.0, "n000002": 3.0, "n000006": 4.0}
    action = s.decide(_ctx(nodes=nodes, metrics=metrics))
    # n000001 (best metric) is pruned; n000002 is next-best. Most-recent is
    # already a child of n000002, so the action is Continue (extending n2).
    assert isinstance(action, Continue)


def test_decide_branchfrom_unpruned_lower_score_when_best_pruned():
    """When the metric-best is pruned, decide() should BranchFrom the next-
    best — and BranchFrom only fires if most-recent isn't already its child."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),     # score 5.0 — pruned
        _node(2, outcome="keep"),     # score 3.0 — viable, but isolated
        _node(3, parent="n000001", outcome="discard"),
        _node(4, parent="n000001", outcome="discard"),
        _node(5, parent="n000001", outcome="discard"),  # most-recent, child of n1
    ]
    metrics = {"n000001": 5.0, "n000002": 3.0}
    action = s.decide(_ctx(nodes=nodes, metrics=metrics))
    assert isinstance(action, BranchFrom)
    assert action.parent_id == "n000002"


def test_decide_all_pruned_returns_stop():
    """If every kept node is pruned, decide() returns Stop with doom-loop reason."""
    s = BFTSLiteStrategy(prune_threshold=3)
    nodes = [
        _node(1, outcome="keep"),
        _node(2, parent="n000001", outcome="discard"),
        _node(3, parent="n000001", outcome="discard"),
        _node(4, parent="n000001", outcome="discard"),
    ]
    metrics = {"n000001": 1.0}
    action = s.decide(_ctx(nodes=nodes, metrics=metrics))
    assert isinstance(action, Stop)
    assert "doom-loop" in action.reason.lower()


def test_make_strategy_forwards_prune_threshold():
    """Factory threads prune_threshold to BFTSLiteStrategy."""
    s = make_strategy("bfts-lite", prune_threshold=5)
    assert isinstance(s, BFTSLiteStrategy)
    assert s.prune_threshold == 5
    # Other strategies ignore the kwarg without error.
    assert isinstance(make_strategy("greedy", prune_threshold=5), GreedyStrategy)


# ---------------------------------------------------------------------------
# StrategyAction immutability + equality
# ---------------------------------------------------------------------------


def test_actions_are_frozen_dataclasses():
    """frozen=True ensures actions can't be mutated post-construction."""
    a = Continue(reason="extending")
    with pytest.raises(Exception):  # FrozenInstanceError, but matching by type is fragile
        a.reason = "tampered"  # type: ignore[misc]


def test_action_equality():
    assert Continue() == Continue()
    assert BranchFrom("n1") == BranchFrom("n1")
    assert BranchFrom("n1") != BranchFrom("n2")
    assert Stop("a") != Stop("b")  # reason matters in equality
