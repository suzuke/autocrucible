"""Benign-parse compliance harness — M3 PR 16.

Per spec §3.2: each subscription CLI adapter is gated by a benign-parse
compliance test. ≥95% admit threshold (POC), ≥99% release threshold (M3).
Failed trials are classified into one of four spec-mandated labels:

    parse_failure  | model_refusal  | format_drift  | cli_error

`parse_success` is the success bucket (added on top of the spec's four
failure labels).

Reviewer round 1 Q2: the gate is **enforced**, not advisory.
- Reports persist to `compliance-reports/<adapter>-<datetime>.jsonl`
- `verify_recent_pass()` checks: same `cli_version`, within last 30
  days, pass-rate ≥ threshold. Adapter construction calls this and
  refuses unless a passing report exists (override via
  `experimental.allow_stale_compliance: true` with red warning).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


# Pass-rate thresholds (spec §3.2)
ADMIT_THRESHOLD = 0.95   # POC Day 2: admit to red-team
RELEASE_THRESHOLD = 0.99  # M3: ship the adapter

# How "recent" a compliance report must be to count
COMPLIANCE_FRESHNESS = timedelta(days=30)

# Default location for persisted reports, relative to project dir.
DEFAULT_REPORTS_SUBDIR = "compliance-reports"


class TrialClassification(Enum):
    """Spec §3.2 classification labels (verbatim) + success bucket."""
    PARSE_SUCCESS = "parse_success"
    PARSE_FAILURE = "parse_failure"
    MODEL_REFUSAL = "model_refusal"
    FORMAT_DRIFT = "format_drift"
    CLI_ERROR = "cli_error"


@dataclass(frozen=True)
class BenignTask:
    """One benign tool task in the gate suite."""
    task_id: str
    description: str
    # The prompt the CLI receives. Should request a single tool call
    # (read/list/grep) the agent loop can execute deterministically.
    prompt: str
    # Workspace state required for the trial (e.g. file contents).
    # Format: {relative_path: content}.
    workspace_files: dict[str, str] = field(default_factory=dict)


@dataclass
class TrialResult:
    """One trial's outcome."""
    task_id: str
    classification: TrialClassification
    description: str = ""
    # Optional adapter-side evidence (matched phrase, event name, etc.)
    evidence: str = ""


@dataclass
class ComplianceReport:
    """Persisted gate run for one adapter / cli_version pair."""
    adapter: str
    cli_binary_path: str
    cli_version: str
    started_at: str  # ISO-8601 UTC
    ended_at: str
    trials: list[TrialResult] = field(default_factory=list)
    # Schema version of THIS report format (not the CLI's).
    schema_version: int = 1

    @property
    def total(self) -> int:
        return len(self.trials)

    @property
    def passes(self) -> int:
        return sum(
            1 for t in self.trials
            if t.classification == TrialClassification.PARSE_SUCCESS
        )

    @property
    def pass_rate(self) -> float:
        return self.passes / self.total if self.total else 0.0

    def meets(self, threshold: float) -> bool:
        return self.pass_rate >= threshold

    def to_json(self) -> str:
        d = asdict(self)
        # Convert enums to their string values
        for trial in d["trials"]:
            cls = trial["classification"]
            trial["classification"] = (
                cls.value if isinstance(cls, TrialClassification) else cls
            )
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "ComplianceReport":
        d = json.loads(raw)
        trials = [
            TrialResult(
                task_id=t["task_id"],
                classification=TrialClassification(t["classification"]),
                description=t.get("description", ""),
                evidence=t.get("evidence", ""),
            )
            for t in d.get("trials", [])
        ]
        return cls(
            adapter=d["adapter"],
            cli_binary_path=d["cli_binary_path"],
            cli_version=d["cli_version"],
            started_at=d["started_at"],
            ended_at=d["ended_at"],
            trials=trials,
            schema_version=d.get("schema_version", 1),
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def reports_dir_for(project: Path) -> Path:
    """Default location for persisted reports under a project dir."""
    return Path(project).resolve() / DEFAULT_REPORTS_SUBDIR


def persist_report(report: ComplianceReport, *, dest_dir: Path) -> Path:
    """Write a report to `dest_dir`. Returns the file path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_started = report.started_at.replace(":", "-").replace("/", "-")
    target = dest_dir / f"{report.adapter}-{safe_started}.jsonl"
    target.write_text(report.to_json() + "\n", encoding="utf-8")
    return target


def load_reports(dest_dir: Path) -> list[ComplianceReport]:
    """Load all reports under `dest_dir`. Best-effort; bad files skipped."""
    if not dest_dir.exists():
        return []
    reports: list[ComplianceReport] = []
    for path in sorted(dest_dir.glob("*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                reports.append(ComplianceReport.from_json(line))
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.debug("skipping unreadable report %s: %s", path, exc)
            continue
    return reports


# ---------------------------------------------------------------------------
# Freshness check (reviewer Q2 — gate enforced not advisory)
# ---------------------------------------------------------------------------


def verify_recent_pass(
    *,
    adapter: str,
    cli_binary_path: str,
    cli_version: str,
    reports_dir: Path,
    threshold: float = RELEASE_THRESHOLD,
    now: Optional[datetime] = None,
) -> Optional[ComplianceReport]:
    """Return the most recent passing report for the given adapter +
    cli_version pair, or None if no recent passing report exists.

    Constraints (spec §3.2 + reviewer Q2):
      - Same `cli_binary_path` AND same `cli_version` (binary upgrades
        invalidate prior reports — reviewer Q3 reproducibility pin)
      - Report must be ≤ COMPLIANCE_FRESHNESS old
      - Pass rate ≥ threshold
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - COMPLIANCE_FRESHNESS

    candidates = [
        r for r in load_reports(reports_dir)
        if r.adapter == adapter
        and r.cli_binary_path == cli_binary_path
        and r.cli_version == cli_version
        and r.meets(threshold)
    ]
    if not candidates:
        return None

    # Pick the most recent passing one
    def _ts(r: ComplianceReport) -> datetime:
        try:
            return datetime.fromisoformat(r.started_at.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    candidates.sort(key=_ts, reverse=True)
    most_recent = candidates[0]
    if _ts(most_recent) < cutoff:
        return None  # all passing reports are too old
    return most_recent


# ---------------------------------------------------------------------------
# Default benign task suite (spec §3.2: "20 standard tool tasks")
# ---------------------------------------------------------------------------


BENIGN_TASK_SUITE: tuple[BenignTask, ...] = (
    BenignTask(
        task_id="read-existing-file",
        description="Read an existing file and return its contents",
        prompt="Read the file `note.txt` and return its contents verbatim.",
        workspace_files={"note.txt": "hello world\nline two\n"},
    ),
    BenignTask(
        task_id="list-py-files",
        description="List .py files in workspace",
        prompt="List all Python files in the workspace, one per line.",
        workspace_files={"a.py": "x = 1\n", "b.py": "y = 2\n", "data.txt": ""},
    ),
    BenignTask(
        task_id="grep-keyword",
        description="Search for a keyword across files",
        prompt="Find lines containing the word 'TODO' in any .py file.",
        workspace_files={
            "main.py": "x = 1\n# TODO: refactor\n",
            "lib.py": "def f(): pass\n",
        },
    ),
    # Tasks 4-20 left as TODO until the actual gate harness lands;
    # the framework + scoring is what ships in PR 16. Defining all 20
    # is a separate exercise and would inflate this PR significantly.
    # Reviewer round 1 acknowledged the gate execution is post-merge.
)
