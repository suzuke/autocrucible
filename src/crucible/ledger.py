"""TrialLedger — append-only attempt-tree storage for Crucible v1.0.

Per design doc v1.0-design-final.md §4 / §11.

Three dataclasses form the on-disk schema:

  - `AttemptNode`: one experiment iteration (commit, model, diff, metric ref…)
  - `EvalResult`:   platform-owned, sealed metric artifact written separately
  - `LedgerRecord`: envelope distinguishing full nodes from state deltas

The ledger file (`logs/run-<tag>/ledger.jsonl`) stores `LedgerRecord` events
one-per-line. Readers tolerate partial last-line truncation.

Concurrency: `TrialLedger.append()` uses `fcntl.flock` to serialise writes
across processes (POSIX only — mac/Linux). Windows is explicitly out of
scope for v1.0 concurrent ledger writes per spec §11 INV-4.

This module is purely additive. No existing crucible code is modified.
M1a PR 2 will wire `orchestrator.py` to write here alongside `ResultsLog`.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import fcntl
import io
import json
import os
import platform
import sys
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Iterable, Iterator, Literal, Optional

# ---------------------------------------------------------------------------
# Type aliases (keep wire-format strings stable; bumping schema_version on change)
# ---------------------------------------------------------------------------

# Aligns with existing config strings ("maximize" / "minimize") so AttemptNode
# can be built directly from a SearchConfig without translation.
MetricDirection = Literal["maximize", "minimize"]

# Experiment outcome — matches the existing ExperimentRecord vocabulary in
# `results.py` so we don't introduce a parallel taxonomy.
Outcome = Literal[
    "keep",
    "discard",
    "crash",
    "violation",
    "skip",
    "budget_exceeded",
    "fatal",
]

# BFTS frontier state — orthogonal to outcome. Frozen at "frontier" for now;
# M1b adds expansion/pruning transitions.
NodeState = Literal["frontier", "expanded", "pruned", "exhausted"]

LedgerEvent = Literal["node", "state_update"]

# Source of usage measurement (see spec §4.1). For CLI subprocess backends we
# may not be able to recover token counts; ledger declares this honestly
# rather than emitting fake zeros.
#
# Values:
#   "api"             — real metered API cost (Anthropic API key, etc.)
#   "cli_estimated"   — CLI subprocess; cost estimated from token counts if
#                       provider surfaces them, else None
#   "oauth_estimated" — M3 PR 19a: smolagents+claude-subscription path.
#                       SDK's ResultMessage.total_cost_usd is the
#                       API-equivalent estimate of what the call would
#                       have cost on metered auth — NOT the actual
#                       subscription bill. Disambiguate from "api"
#                       (real metered cost) so postmortem doesn't
#                       mislead users into thinking they're billed twice.
#   "unavailable"     — backend doesn't / can't report cost
UsageSource = Literal["api", "cli_estimated", "oauth_estimated", "unavailable"]


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

LEDGER_SCHEMA_VERSION = 1
DIFF_TEXT_INLINE_LIMIT_BYTES = 4 * 1024  # capped inline; full diff ref'd via diff_ref


# ---------------------------------------------------------------------------
# AttemptNode (one experiment iteration)
# ---------------------------------------------------------------------------


@dataclass
class AttemptNode:
    """Persistent record of one attempted edit + evaluation.

    Identity: `id` is a short readable identifier (e.g. "n000042") used in
    HTML reports and CLI output. `uuid` is optional and may be set when full
    cross-session uniqueness is required (e.g. cross-machine replay).

    Tree edges: `parent_id` links to the parent attempt (None for the
    baseline / first node). Renderers and replayers MUST rely on `commit`
    (immutable git sha), NOT `branch`, because branches may be cleaned up
    while terminal commits are kept by tag/ref.
    """

    # Identity
    id: str                           # readable short id, e.g. "n000042"
    uuid: Optional[str] = None        # optional UUID4 string

    # Tree edges
    parent_id: Optional[str] = None
    commit: str = ""                  # git sha; required for terminal nodes
    branch: Optional[str] = None      # only frontier/expanded; discarded use tag

    # Backend identity (per spec §4.1 / Day 3 finding F7)
    backend_kind: str = ""            # "litellm" | "claude_sdk" | "cli_subscription"
    backend_version: str = ""         # adapter version tag
    model: str = ""                   # e.g. "anthropic/claude-sonnet-4-6" or "cli:claude-code"

    # Subscription-CLI specific (None for API-key backends)
    cli_binary_path: Optional[str] = None
    cli_version: Optional[str] = None
    cli_argv: Optional[list[str]] = None
    env_allowlist: list[str] = field(default_factory=list)  # names only, never values

    # M3 PR 17: isolation tag for truth-in-labeling (per spec §11.2 Q5
    # resolution). Populated by SubscriptionCLIBackend with
    # "cli_subscription_unsandboxed"; future backends may declare
    # "local_unsafe" / "docker_strict" / etc. None for legacy backends
    # that don't declare isolation status.
    isolation: Optional[str] = None
    # Path (workspace-relative) to the compliance gate JSONL evidence
    # file that admitted this run. None means either the gate was
    # bypassed (allow_stale_compliance) OR the backend doesn't have a
    # compliance gate (e.g. ClaudeCodeAgent SDK). Reporters branch on
    # `isolation is not None and compliance_report_path is None` to
    # surface the "stale compliance" warning banner.
    compliance_report_path: Optional[str] = None

    # Prompt + diff (capped inline; full content via *_ref paths)
    prompt_digest: str = ""           # sha256 of full prompt
    prompt_ref: str = ""              # path to full prompt file
    diff_text: str = ""               # capped at DIFF_TEXT_INLINE_LIMIT_BYTES
    diff_ref: str = ""                # path to full .patch

    # Eval result (platform-owned, sealed)
    eval_result_ref: Optional[str] = None
    eval_result_sha256: Optional[str] = None  # integrity hash; M2 upgrades to HMAC seal

    # Outcome + state
    outcome: Outcome = "skip"
    node_state: NodeState = "frontier"

    # Cost (None when usage_source = "unavailable")
    cost_usd: Optional[float] = None
    usage_source: UsageSource = "unavailable"

    # Timestamps + bookkeeping
    created_at: str = ""              # ISO-8601 UTC
    expanded_at: Optional[str] = None # when BFTS picked node for expansion
    worktree_path: str = ""           # isolated git worktree per attempt

    # Optional human-readable note. Populated for "violation" and "skip"
    # outcomes (where the violation message / skip reason is the only useful
    # information, since there's no commit or diff to inspect). M1b may also
    # use this for the agent's brief description when it's short enough.
    description: Optional[str] = None

    @staticmethod
    def short_id(seq: int) -> str:
        """Generate "n000042" from a numeric sequence."""
        return f"n{seq:06d}"

    def __post_init__(self) -> None:
        # Diff text must not exceed inline limit; if so, caller should have
        # written full diff to `diff_ref` and supplied a truncated `diff_text`.
        if len(self.diff_text.encode("utf-8")) > DIFF_TEXT_INLINE_LIMIT_BYTES:
            raise ValueError(
                f"diff_text exceeds {DIFF_TEXT_INLINE_LIMIT_BYTES} bytes; "
                f"truncate it or use diff_ref"
            )


# ---------------------------------------------------------------------------
# EvalResult (platform-owned, sealed metric artefact)
# ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    """Sealed, platform-written metric artifact.

    Spec §3 / §11 reviewer note: never trust path existence; always recompute
    `stdout_sha256` / `stderr_sha256` / `seal` on read. The Crucible host
    process is the SOLE writer of this file; agent code can read it (depending
    on policy) but cannot author it.

    M1: `seal` = content-hash style integrity check.
    M2: `seal` = HMAC-SHA256 over canonical JSON.
    """

    # Identity / provenance
    schema_version: int = LEDGER_SCHEMA_VERSION
    run_id: str = ""
    attempt_id: str = ""              # FK → AttemptNode.id
    commit: str = ""                  # git sha at time of evaluation

    # Manifest (per spec finding §11): used to detect agent tampering with
    # eval_command / evaluate.py / config between attempts.
    eval_command: str = ""
    eval_manifest_hash: str = ""      # hash({eval_cmd, entry_file_hash, config_hash})

    # Metric
    metric_name: str = ""
    metric_value: Optional[float] = None
    metric_direction: MetricDirection = "maximize"
    diagnostics: dict[str, str | int | float | bool] = field(default_factory=dict)

    # Execution facts
    valid: bool = False
    exit_code: int = 0
    timed_out: bool = False
    duration_ms: int = 0

    # Integrity
    stdout_sha256: str = ""
    stderr_sha256: str = ""
    seal: Optional[str] = None        # M1: integrity hash; M2: HMAC

    created_at: str = ""              # ISO-8601 UTC


# ---------------------------------------------------------------------------
# LedgerRecord envelope
# ---------------------------------------------------------------------------


@dataclass
class LedgerRecord:
    """Envelope wrapping either a full AttemptNode or a state delta.

    Distinguishing shape lets readers parse `ledger.jsonl` deterministically:
      - event=="node":          .node holds the full AttemptNode
      - event=="state_update":  .node_id + .node_state describe a delta

    Bumping `schema_version` invalidates older readers.
    """

    schema_version: int = LEDGER_SCHEMA_VERSION
    event: LedgerEvent = "node"
    node: Optional[AttemptNode] = None
    node_id: Optional[str] = None
    node_state: Optional[NodeState] = None
    created_at: Optional[str] = None

    @classmethod
    def make_node(cls, node: AttemptNode) -> LedgerRecord:
        return cls(event="node", node=node, created_at=_now_iso())

    @classmethod
    def make_state_update(cls, node_id: str, node_state: NodeState) -> LedgerRecord:
        return cls(
            event="state_update",
            node_id=node_id,
            node_state=node_state,
            created_at=_now_iso(),
        )

    # ---- serialisation ----------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(_dataclass_to_dict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> LedgerRecord:
        data = json.loads(raw)
        if data.get("schema_version") != LEDGER_SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(
                f"ledger record schema_version={data.get('schema_version')} "
                f"!= {LEDGER_SCHEMA_VERSION}"
            )
        node_dict = data.pop("node", None)
        rec = cls(**data)
        if node_dict is not None:
            rec.node = AttemptNode(**node_dict)
        return rec


class UnsupportedSchemaVersion(ValueError):
    """Raised when a ledger record uses an incompatible schema_version."""


# ---------------------------------------------------------------------------
# TrialLedger — JSONL append-only writer + reader
# ---------------------------------------------------------------------------


class TrialLedger:
    """Append-only JSONL ledger.

    Concurrency model:
      - `append()` uses `fcntl.flock(LOCK_EX)` to serialise writers (POSIX)
      - Single-threaded callers can omit locking via `lock=False`, but the
        default is safe and the per-call cost is negligible
      - Readers do NOT take a lock; they tolerate partial last lines (a writer
        crash mid-line shows up as a JSON parse error on the LAST record only)

    Windows note (spec §11 INV-4): `fcntl` is unavailable. Calling `append()`
    on Windows will raise `RuntimeError`. v1.0 documents Windows as
    unsupported for concurrent ledger writes; M2+ may add `msvcrt.locking`.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._inproc_lock = threading.Lock()

    # ---- writes -----------------------------------------------------------

    def append_node(self, node: AttemptNode, *, lock: bool = True) -> None:
        self._append(LedgerRecord.make_node(node), lock=lock)

    def update_state(self, node_id: str, state: NodeState, *, lock: bool = True) -> None:
        self._append(LedgerRecord.make_state_update(node_id, state), lock=lock)

    def _append(self, record: LedgerRecord, *, lock: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = record.to_json()
        if "\n" in line:
            raise ValueError("ledger record JSON must be single-line")
        if lock and platform.system() == "Windows":
            raise RuntimeError(
                "TrialLedger.append() with lock=True is unsupported on Windows; "
                "set lock=False for single-process use only"
            )
        # Flush sequence: open append, lock, write, unlock, close.
        with self._inproc_lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                if lock and platform.system() != "Windows":
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                finally:
                    if lock and platform.system() != "Windows":
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ---- reads ------------------------------------------------------------

    def iter_records(self) -> Iterator[LedgerRecord]:
        """Yield each record in order. Tolerates partial last line (skips it)."""
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line_num, raw in enumerate(fh, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    yield LedgerRecord.from_json(stripped)
                except json.JSONDecodeError:
                    # Could be torn last write. Only tolerated on the LAST
                    # line — earlier lines that fail to parse re-raise.
                    # We can't peek ahead in a single-pass iterator without
                    # buffering the whole file, so we DEFER the decision:
                    # collect remaining lines and only suppress error if this
                    # is the last non-empty line.
                    rest = fh.read()
                    if rest.strip():
                        # there was more content after this bad line ->
                        # propagate the parse error
                        raise
                    return  # treat torn last line as EOF

    def all_nodes(self) -> list[AttemptNode]:
        """Flatten the ledger to a list of AttemptNode (latest state delta wins)."""
        nodes: dict[str, AttemptNode] = {}
        for rec in self.iter_records():
            if rec.event == "node" and rec.node is not None:
                nodes[rec.node.id] = rec.node
            elif rec.event == "state_update" and rec.node_id is not None:
                if rec.node_id in nodes and rec.node_state is not None:
                    nodes[rec.node_id].node_state = rec.node_state
        return list(nodes.values())

    def children_of(self, parent_id: Optional[str]) -> list[AttemptNode]:
        return [n for n in self.all_nodes() if n.parent_id == parent_id]

    # ------------------------------------------------------------------
    # M3 PR 17: query helpers for postmortem / compare / analysis tools
    # ------------------------------------------------------------------

    def kept_path(
        self, node_id: str, *, include_self: bool = True
    ) -> list[AttemptNode]:
        """Walk the parent chain from `node_id`, return only kept ancestors.

        Result is ordered oldest-first (root → ... → node_id).

        Args:
            node_id: AttemptNode id to start the walk from.
            include_self: when True (default), the queried node is in
                the result iff its outcome is "keep". When False, the
                queried node is dropped from the result regardless of
                its outcome — useful when callers want only ancestors.

        Reviewer round 1 Q2 pin: walk the parent chain unconditionally,
        filter to outcome=="keep", drop self when requested. Don't bake
        heuristics into the helper.

        Defensive: orphan nodes (parent_id set but parent missing from
        ledger) terminate the walk cleanly. Cycles (shouldn't happen
        but mirror `_render_tree`'s defense) trip a `visited` guard.
        """
        nodes_by_id = {n.id: n for n in self.all_nodes()}
        chain: list[AttemptNode] = []
        visited: set[str] = set()
        cur_id: Optional[str] = node_id
        while cur_id and cur_id not in visited:
            visited.add(cur_id)
            node = nodes_by_id.get(cur_id)
            if node is None:
                break
            chain.append(node)
            cur_id = node.parent_id
        # chain is currently node_id → root order; reverse to root → node_id
        chain.reverse()
        # Filter to keep-only
        chain = [n for n in chain if n.outcome == "keep"]
        if not include_self and chain and chain[-1].id == node_id:
            chain = chain[:-1]
        return chain

    def descendants_of(self, node_id: str) -> list[AttemptNode]:
        """Return all transitive children of `node_id`, DFS order.

        Iteration order matches `_render_tree` in `html_tree.py`:
        DFS-by-parent with siblings sorted by id (sequential ids →
        insertion order). Reviewer round 1 Q2 pin: documented order
        so callers can rely on it; if BFS is needed later, add a
        separate helper rather than parameterizing.

        Defensive: cycles trip a `visited` guard (mirrors
        `_render_tree`).
        """
        all_nodes = self.all_nodes()
        by_parent: dict[Optional[str], list[AttemptNode]] = {}
        for n in all_nodes:
            by_parent.setdefault(n.parent_id, []).append(n)
        for siblings in by_parent.values():
            siblings.sort(key=lambda n: (n.id, n.created_at))

        out: list[AttemptNode] = []
        visited: set[str] = set()

        def walk(parent: str) -> None:
            for child in by_parent.get(parent, []):
                if child.id in visited:
                    continue
                visited.add(child.id)
                out.append(child)
                walk(child.id)

        walk(node_id)
        return out

    def find_by_outcome(self, outcome: str) -> list[AttemptNode]:
        """Return nodes whose outcome matches `outcome` exactly.

        Outcomes are documented as `Literal[...]` in spec §4.1; this
        helper accepts plain str for forward-compat with custom
        outcomes future strategies might emit.
        """
        return [n for n in self.all_nodes() if n.outcome == outcome]

    def best_node(self, direction: MetricDirection = "maximize",
                  metric_lookup: Optional[dict[str, float]] = None) -> Optional[AttemptNode]:
        """Best-metric node among "keep" outcomes.

        Because `AttemptNode` does not carry `metric_value` directly (the
        sealed `EvalResult` does), callers pass a `metric_lookup` from
        `attempt_id → metric_value`. The ledger does not import EvalResult
        unconditionally — that coupling is M1b's job.
        """
        kept = [n for n in self.all_nodes() if n.outcome == "keep"]
        if not kept:
            return None
        if metric_lookup is None:
            return kept[0]
        candidates = [(metric_lookup.get(n.id), n) for n in kept]
        candidates = [(m, n) for m, n in candidates if m is not None]
        if not candidates:
            return None
        if direction == "maximize":
            return max(candidates, key=lambda pair: pair[0])[1]
        return min(candidates, key=lambda pair: pair[0])[1]

    def frontier(self) -> list[AttemptNode]:
        return [n for n in self.all_nodes() if n.node_state == "frontier"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _dataclass_to_dict(obj) -> dict:
    """asdict() that handles nested dataclasses and skips None values cleanly."""
    if dataclasses.is_dataclass(obj):
        return {f.name: _dataclass_to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [_dataclass_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj
