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

# Module-level import (NOT lazy): keeps the binding stable across
# sys.modules eviction by tests like test_agents_factory. If the import
# becomes lazy inside the harness function body, the harness's
# `except CLISubscriptionAuthError as exc:` resolves to a freshly-
# imported class object after eviction — which doesn't match exception
# instances raised by callers that hold the pre-eviction class.
from crucible.agents.cli_subscription.base import (
    AdapterRunContext,
    CLISubscriptionAuthError,
)

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
    """Load all reports under `dest_dir`. Best-effort; bad files skipped.

    NOTE: returns reports without source-path attribution. For audit
    trail purposes (reviewer round 2 Bug #2 — `compliance_report_path`
    metadata field), use `load_reports_with_paths()` instead.
    """
    return [report for report, _path in load_reports_with_paths(dest_dir)]


def load_reports_with_paths(dest_dir: Path) -> list[tuple["ComplianceReport", Path]]:
    """Load all reports + their source file paths.

    Reviewer round 2 Bug #2: the AttemptNode metadata field
    `compliance_report_path` MUST be the report file path, not the CLI
    binary path. This loader returns the (report, source_path) pairs so
    `verify_recent_pass` can thread the path through to the backend.
    """
    if not dest_dir.exists():
        return []
    out: list[tuple[ComplianceReport, Path]] = []
    for path in sorted(dest_dir.glob("*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                out.append((ComplianceReport.from_json(line), path))
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.debug("skipping unreadable report %s: %s", path, exc)
            continue
    return out


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

    Returns just the report. For the report's source file path (audit
    trail), use `verify_recent_pass_with_path` instead — reviewer
    round 2 Bug #2 fix.
    """
    pair = verify_recent_pass_with_path(
        adapter=adapter,
        cli_binary_path=cli_binary_path,
        cli_version=cli_version,
        reports_dir=reports_dir,
        threshold=threshold,
        now=now,
    )
    return pair[0] if pair else None


def verify_recent_pass_with_path(
    *,
    adapter: str,
    cli_binary_path: str,
    cli_version: str,
    reports_dir: Path,
    threshold: float = RELEASE_THRESHOLD,
    now: Optional[datetime] = None,
) -> Optional[tuple[ComplianceReport, Path]]:
    """Like `verify_recent_pass` but also returns the report file path.

    Reviewer round 2 Bug #2: the AttemptNode metadata field
    `compliance_report_path` MUST be the path to the JSONL report file
    on disk so auditors can follow the trail to the gate evidence.
    Previously the backend was incorrectly writing the CLI binary path
    into that metadata field.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - COMPLIANCE_FRESHNESS

    candidates = [
        (r, p) for r, p in load_reports_with_paths(reports_dir)
        if r.adapter == adapter
        and r.cli_binary_path == cli_binary_path
        and r.cli_version == cli_version
        and r.meets(threshold)
    ]
    if not candidates:
        return None

    def _ts(pair) -> datetime:
        r = pair[0]
        try:
            return datetime.fromisoformat(r.started_at.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    candidates.sort(key=_ts, reverse=True)
    most_recent_report, most_recent_path = candidates[0]
    if _ts(candidates[0]) < cutoff:
        return None
    return most_recent_report, most_recent_path


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


# ---------------------------------------------------------------------------
# Harness runner — M3 PR 16c
# ---------------------------------------------------------------------------


# Phrases that signal model refusal of a benign task. Matched
# intentionally against an explicitly declared set (PR 19 R2 lesson).
# Conservative: only obvious refusals, not edge cases. The harness
# falls back to PARSE_SUCCESS if `tool_was_called` is True regardless.
_MODEL_REFUSAL_PHRASES = (
    "I can't",
    "I cannot",
    "I'm not able to",
    "I am not able to",
    "I won't",
    "I will not",
    "Sorry, I",
    "I'm sorry, I can",
    "unable to comply",
    "refuse to",
)


def _classify_trial(
    raw: "AdapterRawResult",  # type: ignore[name-defined]
    parsed: Optional["ParsedAdapterOutput"],  # type: ignore[name-defined]
    auth_error_evidence: Optional[str],
) -> tuple[TrialClassification, str]:
    """Classify one trial outcome into a spec §3.2 label.

    Order matters — earliest-matching wins:
      1. CLI_ERROR for hard failures: timeout, auth-failure, non-zero
         exit (auth failure DURING the gate run means the gate run is
         invalid, not that the adapter is non-compliant — but we still
         classify as CLI_ERROR so the operator sees the gate as
         non-passing rather than silently scoring it).
      2. PARSE_FAILURE for unknown_schema (parser saw events it didn't
         recognise → schema drift).
      3. PARSE_SUCCESS if a tool was called (the CLI engaged the tool
         loop, the adapter parsed it, all good).
      4. MODEL_REFUSAL if the description contains a declared refusal
         phrase AND no tool was called.
      5. FORMAT_DRIFT for the residual: CLI ran, exited cleanly, parser
         was happy, but no tool engagement and no refusal phrase. The
         CLI returned text instead of using its tool surface.
    """
    if raw.timed_out:
        return TrialClassification.CLI_ERROR, "subprocess timeout"
    if auth_error_evidence is not None:
        return TrialClassification.CLI_ERROR, f"auth: {auth_error_evidence[:200]}"
    if raw.exit_code != 0:
        return TrialClassification.CLI_ERROR, f"exit_code={raw.exit_code}"
    if parsed is None:
        return TrialClassification.CLI_ERROR, "parser raised"
    if parsed.unknown_schema:
        return TrialClassification.PARSE_FAILURE, "unknown_schema"
    if parsed.tool_was_called:
        return TrialClassification.PARSE_SUCCESS, ""
    desc = parsed.description or ""
    for phrase in _MODEL_REFUSAL_PHRASES:
        if phrase in desc:
            return TrialClassification.MODEL_REFUSAL, f"phrase: {phrase!r}"
    return TrialClassification.FORMAT_DRIFT, "no tool call, no refusal phrase"


def run_compliance_harness(
    adapter,  # SubscriptionCLIAdapter; type avoided to keep import surface narrow
    *,
    tasks: Sequence[BenignTask] = BENIGN_TASK_SUITE,
    project_dir: Path,
    timeout_seconds: int = 60,
    stdout_cap_bytes: int = 1_000_000,
) -> ComplianceReport:
    """Run the benign-parse compliance gate against `adapter`.

    For each task in `tasks`:
      1. Materialise `task.workspace_files` into a temp scratch dir.
      2. Build an AdapterRunContext pointing at the scratch dir.
      3. Call `adapter.run_subprocess(ctx)` to invoke the CLI.
      4. Call `adapter.parse_output(raw)` (catching CLISubscriptionAuthError).
      5. Classify the trial via `_classify_trial`.
      6. Accumulate into ComplianceReport.

    Persists the report to `reports_dir_for(project_dir)` and returns it.
    Raises nothing on individual trial failures — the gate's purpose is
    to MEASURE the adapter's behaviour, not to short-circuit on first
    error. Genuine framework errors (binary missing, dir permissions)
    propagate.

    NB: the harness intentionally does NOT use `SubscriptionCLIBackend`'s
    full pipeline (scratch_dir context manager + copy-back + safety
    filter). The compliance gate is about parser conformance against
    benign tasks; it doesn't need policy ACL enforcement.
    """
    import tempfile

    started_at = datetime.now(tz=timezone.utc).isoformat()
    trials: list[TrialResult] = []

    for task in tasks:
        with tempfile.TemporaryDirectory(
            prefix=f"crucible-compliance-{task.task_id}-",
        ) as tmpdir:
            scratch = Path(tmpdir)
            for rel, content in task.workspace_files.items():
                p = scratch / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)

            ctx = AdapterRunContext(
                prompt=task.prompt,
                scratch_dir=scratch,
                workspace_root=scratch,
                timeout_seconds=timeout_seconds,
                stdout_cap_bytes=stdout_cap_bytes,
            )

            try:
                raw = adapter.run_subprocess(ctx)
            except Exception as exc:
                # Framework-level failure (binary not found mid-run,
                # OS permissions). Classify as CLI_ERROR with the
                # exception text for forensics.
                logger.warning(
                    "compliance: %s subprocess raised: %s", task.task_id, exc,
                )
                trials.append(TrialResult(
                    task_id=task.task_id,
                    classification=TrialClassification.CLI_ERROR,
                    description=f"subprocess raise: {type(exc).__name__}",
                    evidence=str(exc)[:500],
                ))
                continue

            parsed = None
            auth_evidence: Optional[str] = None
            try:
                parsed = adapter.parse_output(raw)
            except CLISubscriptionAuthError as exc:
                auth_evidence = exc.evidence
            except Exception as exc:
                logger.warning(
                    "compliance: %s parser raised: %s", task.task_id, exc,
                )
                trials.append(TrialResult(
                    task_id=task.task_id,
                    classification=TrialClassification.CLI_ERROR,
                    description=f"parser raise: {type(exc).__name__}",
                    evidence=str(exc)[:500],
                ))
                continue

            classification, evidence = _classify_trial(raw, parsed, auth_evidence)
            description = (parsed.description if parsed else "")[:500]
            trials.append(TrialResult(
                task_id=task.task_id,
                classification=classification,
                description=description,
                evidence=evidence,
            ))

    ended_at = datetime.now(tz=timezone.utc).isoformat()
    report = ComplianceReport(
        adapter=adapter.cli_name,
        cli_binary_path=str(adapter.cli_binary_path),
        cli_version=adapter.cli_version,
        started_at=started_at,
        ended_at=ended_at,
        trials=trials,
    )

    dest_dir = reports_dir_for(project_dir)
    persist_report(report, dest_dir=dest_dir)
    return report
