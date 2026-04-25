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
    consecutive_failures: int
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
        if ctx.consecutive_failures >= ctx.plateau_threshold:
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
        if ctx.consecutive_failures >= ctx.plateau_threshold:
            return Restart(reason=f"plateau_threshold={ctx.plateau_threshold} hit")
        return Continue()

    def should_prune(self, ctx: StrategyContext, candidate_id: str) -> bool:
        return False


@dataclass
class BFTSLiteStrategy:
    """Best-first tree search, M1b minimum. NOT YET WIRED into orchestrator;
    will be activated in M1b PR 2.

    Algorithm:
      1. Identify all kept nodes (`outcome == "keep"`) with metric_lookup.
      2. Pick the best one not currently the parent of the most recent node.
      3. If it's not the most recent ancestor, return BranchFrom(best_id);
         otherwise return Continue.
      4. Stop when no kept nodes exist or max_iterations reached.

    Pruning is a no-op in M1b PR 1; M2 will plug in doom-loop pruning.
    """

    name: str = "bfts-lite"

    def decide(self, ctx: StrategyContext) -> StrategyAction:
        if ctx.max_iterations is not None and ctx.iteration_count >= ctx.max_iterations:
            return Stop(reason=f"max_iterations={ctx.max_iterations} reached")

        kept = [n for n in ctx.ledger_nodes
                if n.outcome == "keep" and n.id in ctx.metric_lookup]
        if not kept:
            return Continue()  # nothing to branch from yet

        # Best kept node by direction
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
        return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_STRATEGIES: dict[str, type] = {
    "greedy": GreedyStrategy,
    "restart": RestartStrategy,
    "bfts-lite": BFTSLiteStrategy,
}


def make_strategy(name: str) -> SearchStrategy:
    """Build a strategy instance by name. Raises ValueError on unknown name."""
    cls = _STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"unknown search strategy: {name!r} "
                         f"(available: {sorted(_STRATEGIES)})")
    return cls()
