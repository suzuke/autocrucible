"""Strategy decision sidecar log — M3 PR 17.

Reviewer round 1 Q4 pin: this is a SEPARATE sidecar file alongside
`ledger.jsonl`, NOT a new event type in the ledger itself.

  Why not the ledger:
  - Ledger is safety-critical (POSIX flock, single-writer, schema-
    versioned, crash-recoverable, the seal HMAC depends on stable
    shape). Adding debug-only events forces a schema bump and competes
    for the write lock with attempt commits.
  - Schema bump invalidates parallel-append safety per spec §4.2.

  Why not in-memory:
  - Lost on crash exactly when you most need to debug.
  - Can't replay or diff strategies across runs.

  Sidecar wins on all three counts and is reviewer-blessed.

The sidecar lives at `logs/run-<tag>/strategy-decisions.jsonl`. Each
line is one `StrategyDecision` JSON record. The reporter looks for
the file via deterministic path; absent file = strategy didn't log
decisions for this run (older run, different strategy, etc).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SIDECAR_FILENAME = "strategy-decisions.jsonl"


@dataclass
class StrategyDecision:
    """One strategy.decide() call recorded for offline analysis.

    Fields chosen to answer the operator's typical postmortem
    question: "why did BFTS branch back from X to Y at iter 17?"
      - `iteration`: which iteration this decision was made for
      - `kept_candidates`: AttemptNode ids the strategy considered
      - `pruned_candidates`: ids that were filtered out by
        `should_prune` (M2 PR 10 doom-loop pruning)
      - `chosen_action`: short string e.g. "continue" / "branch_from"
        / "restart" / "stop"
      - `rationale`: one-line human-readable summary
      - `extras`: free-form dict for adapter-specific details
    """
    timestamp: str       # ISO-8601 UTC
    iteration: int
    kept_candidates: list[str] = field(default_factory=list)
    pruned_candidates: list[str] = field(default_factory=list)
    chosen_action: str = ""
    rationale: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "StrategyDecision":
        d = json.loads(raw)
        return cls(
            timestamp=d.get("timestamp", ""),
            iteration=int(d.get("iteration", 0)),
            kept_candidates=list(d.get("kept_candidates", [])),
            pruned_candidates=list(d.get("pruned_candidates", [])),
            chosen_action=str(d.get("chosen_action", "")),
            rationale=str(d.get("rationale", "")),
            extras=dict(d.get("extras", {})),
        )

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sidecar_path_for(run_dir: Path | str) -> Path:
    """Return the sidecar JSONL path for a given run-dir."""
    return Path(run_dir) / SIDECAR_FILENAME


def append(run_dir: Path | str, decision: StrategyDecision) -> None:
    """Append a decision record to the sidecar (creates file if needed)."""
    target = sidecar_path_for(run_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(decision.to_json() + "\n")


def load_all(run_dir: Path | str) -> list[StrategyDecision]:
    """Read all sidecar records for a run. Returns [] if file absent."""
    target = sidecar_path_for(run_dir)
    if not target.exists():
        return []
    out: list[StrategyDecision] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(StrategyDecision.from_json(line))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            # Tolerate one bad line per call, mirror ledger reader behavior
            continue
    return out
