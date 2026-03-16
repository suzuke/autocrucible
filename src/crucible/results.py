"""Results module for JSONL experiment logging."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)


def results_filename(tag: str) -> str:
    """Return the results JSONL filename for a given experiment tag."""
    return f"results-{tag}.jsonl"


@dataclass
class UsageInfo:
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None


@dataclass
class ExperimentRecord:
    commit: str
    metric_value: float
    status: str
    description: str
    iteration: int | None = None
    timestamp: str | None = None
    delta: float | None = None
    delta_percent: float | None = None
    files_changed: list[str] | None = None
    diff_stats: dict | None = None
    duration_seconds: float | None = None
    usage: UsageInfo | None = None
    log_dir: str | None = None


def _serialize_record(record: ExperimentRecord) -> str:
    """Serialize an ExperimentRecord to a JSON string, skipping None values."""
    d = asdict(record)
    def _strip_nones(obj):
        if isinstance(obj, dict):
            return {k: _strip_nones(v) for k, v in obj.items() if v is not None}
        return obj
    cleaned = _strip_nones(d)
    return json.dumps(cleaned)


def _deserialize_record(line: str) -> ExperimentRecord:
    """Deserialize a JSON line into an ExperimentRecord."""
    d = json.loads(line)
    # Handle nested UsageInfo
    if "usage" in d and isinstance(d["usage"], dict):
        d["usage"] = UsageInfo(**d["usage"])
    return ExperimentRecord(**{
        f.name: d.get(f.name) for f in fields(ExperimentRecord)
    })


def _parse_jsonl(content: str) -> list[ExperimentRecord]:
    """Parse JSONL content into experiment records."""
    records: list[ExperimentRecord] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(_deserialize_record(line))
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.warning("Skipping malformed JSONL line: %s", e)
            continue
    return records


# Keep TSV header constant for backward compat detection
HEADER = "commit\tmetric_value\tstatus\tdescription"


def _parse_tsv(content: str) -> list[ExperimentRecord]:
    """Parse TSV content into experiment records (skips header). Backward compat."""
    records: list[ExperimentRecord] = []
    for line in content.splitlines()[1:]:
        if not line.strip():
            continue
        parts = line.split("\t", maxsplit=3)
        if len(parts) < 4:
            continue
        try:
            metric = float(parts[1])
        except ValueError:
            continue
        records.append(
            ExperimentRecord(
                commit=parts[0],
                metric_value=metric,
                status=parts[2],
                description=parts[3],
            )
        )
    return records


class ResultsLog:
    """Append-only JSONL log of experiment results."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._cache: list[ExperimentRecord] | None = None
        self._cache_mtime: float | None = None

    def init(self) -> None:
        """Create an empty JSONL file (no header line)."""
        self.path.write_text("")

    def seed_baseline(self, value: float, commit: str, source_tag: str) -> None:
        """Write a baseline record from a previous run's best result."""
        record = ExperimentRecord(
            commit=commit,
            metric_value=value,
            status="baseline",
            description=f"Forked from {source_tag} best",
        )
        self.log(record)

    def log(self, record: ExperimentRecord) -> None:
        """Append one experiment record to the log."""
        line = _serialize_record(record) + "\n"
        with self.path.open("a") as f:
            f.write(line)

    def read_all(self) -> list[ExperimentRecord]:
        """Read every record from the log.

        Results are cached by file mtime so that multiple calls within
        the same iteration (between writes) avoid re-reading and
        re-parsing the file.
        """
        if not self.path.exists():
            return []
        mtime = self.path.stat().st_mtime
        if self._cache is not None and self._cache_mtime == mtime:
            return self._cache
        records = _parse_jsonl(self.path.read_text())
        self._cache = records
        self._cache_mtime = mtime
        return records

    def read_last(self, n: int) -> list[ExperimentRecord]:
        """Return the last *n* records."""
        records = self.read_all()
        return records[-n:]

    def best(self, direction: str) -> ExperimentRecord | None:
        """Return the best record among those with status 'keep' or 'baseline'.

        *direction* is ``"minimize"`` or ``"maximize"``.
        """
        candidates = [r for r in self.read_all() if r.status in ("keep", "baseline")]
        if not candidates:
            return None
        if direction == "minimize":
            return min(candidates, key=lambda r: r.metric_value)
        return max(candidates, key=lambda r: r.metric_value)

    def is_improvement(self, value: float, direction: str) -> bool:
        """Check whether *value* improves on the current best.

        Returns ``True`` if there are no previous records.
        """
        current_best = self.best(direction)
        if current_best is None:
            return True
        if direction == "minimize":
            return value < current_best.metric_value
        return value > current_best.metric_value

    @staticmethod
    def read_from_string(content: str) -> list[ExperimentRecord]:
        """Parse records from string content, auto-detecting JSONL vs TSV format."""
        # Auto-detect: check if first non-empty line starts with '{'
        for line in content.splitlines():
            stripped = line.strip()
            if stripped:
                if stripped.startswith("{"):
                    return _parse_jsonl(content)
                else:
                    return _parse_tsv(content)
        return []

    def summary(self) -> dict[str, int]:
        """Return counts by status category (excludes baseline)."""
        records = [r for r in self.read_all() if r.status != "baseline"]
        return {
            "total": len(records),
            "kept": sum(1 for r in records if r.status == "keep"),
            "discarded": sum(1 for r in records if r.status == "discard"),
            "crashed": sum(1 for r in records if r.status == "crash"),
        }
