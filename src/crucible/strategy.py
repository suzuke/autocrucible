"""SearchStrategy Protocol — M1b PR 1 (interface only, additive).

The strategy interface that BFTS will implement in M1b PR 2+. Existing
greedy / restart / beam logic in `orchestrator.py` is NOT yet refactored
to call this Protocol; that's a follow-up PR. This PR establishes the
shape of the contract so future strategies can be written and tested
in isolation.

Design notes (per v1.0-design-final.md §M1b):

  - Strategies receive a TrialLedger snapshot and emit a `StrategyAction`
    describing what should happen next. The orchestrator owns the actual
    git/agent/eval mechanics — strategies are pure decisions over history.

  - Action types cover the four shapes M1b needs:
      Continue        — extend current branch
      BranchFrom(id)  — start a new branch from an existing kept node
      Restart         — back to baseline (existing "restart" behavior)
      Stop            — end the run cleanly

  - `should_prune` is a separate hook for doom-loop / similarity-based
    pruning (M2 hardens it; M1b PR 1 just defines the seam).

  - Strategies are pure (no I/O, no side effects). The orchestrator may
    cache or memoize them across iterations safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Protocol, Sequence, runtime_checkable

from crucible.ledger import AttemptNode


# ---------------------------------------------------------------------------
# Action ADT (sum type)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Continue:
    """Extend the current branch — next iteration runs from the most recent
    kept node (linear behavior)."""

    reason: str = ""


@dataclass(frozen=True)
class BranchFrom:
    """Start a new branch from `parent_id`. Used by BFTS to expand a non-
    most-recent kept node — the orchestrator does `git checkout <commit>`
    and runs the next iteration from there."""

    parent_id: str
    reason: str = ""


@dataclass(frozen=True)
class Restart:
    """Reset to the baseline commit. Same semantics as the existing
    config.search.strategy=="restart" behavior."""

    reason: str = ""


@dataclass(frozen=True)
class Stop:
    """End the run cleanly. Returned when the strategy decides no further
    iteration is worthwhile (plateau, exhaustion, BFTS frontier empty)."""

    reason: str = ""


StrategyAction = Continue | BranchFrom | Restart | Stop


# ---------------------------------------------------------------------------
# Strategy context — what each decide() call sees
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyContext:
    """Snapshot passed to SearchStrategy on each decision.

    Strategies do NOT receive the orchestrator or live git/runner objects
    — only the ledger view and current config. This keeps strategies
    testable in isolation (unit tests don't need a workspace).
    """

    ledger_nodes: Sequence[AttemptNode]
    metric_lookup: dict[str, float]
    metric_direction: Literal["maximize", "minimize"]
    iteration_count: int  # number of iterations completed so far
    plateau_streak: int
    plateau_threshold: int  # config.search.plateau_threshold
    max_iterations: Optional[int]
    baseline_commit: Optional[str]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SearchStrategy(Protocol):
    """The interface every search strategy must satisfy.

    Implementations are stateless (or hold only their config); the
    orchestrator passes a fresh StrategyContext each call.
    """

    name: str
    """Human-readable identifier, e.g. "greedy" / "restart" / "bfts-lite"."""

    def decide(self, ctx: StrategyContext) -> StrategyAction:
        """Decide what the orchestrator should do next.

        Called AFTER each iteration's outcome has been written to the
        ledger and BEFORE the next iteration starts. Must return a
        StrategyAction. Pure function — no I/O, no side effects.
        """
        ...

    def should_prune(self, ctx: StrategyContext, candidate_id: str) -> bool:
        """Optional hook for similarity / doom-loop pruning.

        Called before BranchFrom expansions. If True, the strategy is
        signalling that `candidate_id` is not worth re-expanding (e.g.,
        recent attempts produced the same diff).

        Default implementation (no pruning) returns False.
        """
        ...


# ---------------------------------------------------------------------------
# Reference implementations
# ---------------------------------------------------------------------------


@dataclass
class GreedyStrategy:
    """Continues until plateau or max_iterations; never branches.

    Equivalent to the existing config.search.strategy=="greedy" branch in
    orchestrator.py. Provided here so future code can switch to the
    Protocol without functional change.
    """

    name: str = "greedy"

    def decide(self, ctx: StrategyContext) -> StrategyAction:
        if ctx.max_iterations is not None and ctx.iteration_count >= ctx.max_iterations:
            return Stop(reason=f"max_iterations={ctx.max_iterations} reached")
        if ctx.plateau_streak >= ctx.plateau_threshold:
            return Stop(reason=f"plateau_threshold={ctx.plateau_threshold} hit")
        return Continue()

    def should_prune(self, ctx: StrategyContext, candidate_id: str) -> bool:
        return False


@dataclass
class RestartStrategy:
    """Like greedy but resets to baseline when consecutive failures hit
    plateau_threshold instead of stopping."""

    name: str = "restart"

    def decide(self, ctx: StrategyContext) -> StrategyAction:
        if ctx.max_iterations is not None and ctx.iteration_count >= ctx.max_iterations:
            return Stop(reason=f"max_iterations={ctx.max_iterations} reached")
        if ctx.plateau_streak >= ctx.plateau_threshold:
            return Restart(reason=f"plateau_threshold={ctx.plateau_threshold} hit")
        return Continue()

    def should_prune(self, ctx: StrategyContext, candidate_id: str) -> bool:
        return False


@dataclass
class BFTSLiteStrategy:
    """Best-first tree search.

    Algorithm:
      1. Identify all kept nodes (`outcome == "keep"`) with metric_lookup.
      2. Filter out doom-looped candidates via `should_prune`.
      3. Pick the best surviving one; if not currently the parent of the
         most recent node, return BranchFrom(best_id), else Continue.
      4. If every kept node is pruned, return Stop ("doom-loop").
      5. Stop when no kept nodes exist or max_iterations reached.

    M2 PR 10 plugs in doom-loop pruning via `prune_threshold`.
    """

    name: str = "bfts-lite"
    prune_threshold: int = 3

    def decide(self, ctx: StrategyContext) -> StrategyAction:
        if ctx.max_iterations is not None and ctx.iteration_count >= ctx.max_iterations:
            return Stop(reason=f"max_iterations={ctx.max_iterations} reached")

        kept_all = [n for n in ctx.ledger_nodes
                    if n.outcome == "keep" and n.id in ctx.metric_lookup]
        if not kept_all:
            return Continue()  # nothing to branch from yet

        # M2 PR 10: filter pruned BEFORE max-metric selection so a high-
        # scoring but doom-looped branch cannot starve a viable lower-scoring
        # one.
        kept = [n for n in kept_all if not self.should_prune(ctx, n.id)]
        if not kept:
            return Stop(reason="all kept nodes pruned (doom-loop)")

        chooser = min if ctx.metric_direction == "minimize" else max
        best = chooser(kept, key=lambda n: ctx.metric_lookup[n.id])

        # If the most recent ledger node is already a child of best, just
        # continue extending; otherwise branch from best.
        if not ctx.ledger_nodes:
            return Continue()
        most_recent = ctx.ledger_nodes[-1]
        if most_recent.parent_id == best.id or most_recent.id == best.id:
            return Continue()

        return BranchFrom(parent_id=best.id, reason="BFTS expand best kept node")

    def should_prune(self, ctx: StrategyContext, candidate_id: str) -> bool:
        """Doom-loop pruning over trailing children of `candidate_id`.

        A candidate is pruned when its last `prune_threshold` direct children
        (in ledger order) all failed to produce a strict metric improvement
        over the candidate's own metric.

        Failure shapes counted toward the streak:
          - `discard` outcome (always — regardless of metric)
          - `crash` / `violation` / `skip` outcome (no metric to compare;
            still a failed expansion attempt)
          - `keep` outcome whose metric did NOT strictly improve over the
            candidate's metric (equality counts as non-improvement)

        Defensive corner cases:
          - Returns False if the candidate has no metric (bootstrap nodes
            cannot be reasoned about; "strict improvement over parent
            metric" is undefined).
          - Returns False if direct-child count < prune_threshold (need
            enough evidence before pruning).
          - A strict improvement at any tail position resets the streak.
        """
        candidate_metric = ctx.metric_lookup.get(candidate_id)
        if candidate_metric is None:
            return False

        children = [n for n in ctx.ledger_nodes if n.parent_id == candidate_id]
        if len(children) < self.prune_threshold:
            return False

        direction = ctx.metric_direction
        streak = 0
        for child in reversed(children):
            improved = False
            if child.outcome == "keep":
                child_metric = ctx.metric_lookup.get(child.id)
                if child_metric is not None:
                    if direction == "maximize":
                        improved = child_metric > candidate_metric
                    else:
                        improved = child_metric < candidate_metric
            if improved:
                break
            streak += 1
            if streak >= self.prune_threshold:
                return True
        return streak >= self.prune_threshold


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_STRATEGIES: dict[str, type] = {
    "greedy": GreedyStrategy,
    "restart": RestartStrategy,
    "bfts-lite": BFTSLiteStrategy,
}


def make_strategy(name: str, *, prune_threshold: int = 3) -> SearchStrategy:
    """Build a strategy instance by name. Raises ValueError on unknown name.

    `prune_threshold` is forwarded to BFTSLiteStrategy; other strategies
    ignore it (they don't prune in v1.0).
    """
    cls = _STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"unknown search strategy: {name!r} "
                         f"(available: {sorted(_STRATEGIES)})")
    if cls is BFTSLiteStrategy:
        return cls(prune_threshold=prune_threshold)
    return cls()
