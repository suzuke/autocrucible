"""Results module for TSV experiment logging."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


HEADER = "commit\tmetric_value\tstatus\tdescription"


def results_filename(tag: str) -> str:
    """Return the results TSV filename for a given experiment tag."""
    return f"results-{tag}.tsv"


@dataclass
class ExperimentRecord:
    commit: str
    metric_value: float
    status: str
    description: str


def _parse_records(content: str) -> list[ExperimentRecord]:
    """Parse TSV content into experiment records (skips header)."""
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
    """Append-only TSV log of experiment results."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def init(self) -> None:
        """Create the TSV file with its header row."""
        self.path.write_text(HEADER + "\n")

    def seed_baseline(self, value: float, commit: str, source_tag: str) -> None:
        """Write a baseline record from a previous run's best result."""
        self.log(
            commit=commit,
            metric_value=value,
            status="baseline",
            description=f"Forked from {source_tag} best",
        )

    def log(
        self,
        commit: str,
        metric_value: float,
        status: str,
        description: str,
    ) -> None:
        """Append one experiment record to the log."""
        line = f"{commit}\t{metric_value}\t{status}\t{description}\n"
        with self.path.open("a") as f:
            f.write(line)

    def read_all(self) -> list[ExperimentRecord]:
        """Read every record from the log (excluding the header)."""
        if not self.path.exists():
            return []
        return _parse_records(self.path.read_text())

    def read_last(self, n: int) -> list[ExperimentRecord]:
        """Return the last *n* records."""
        records = self.read_all()
        return records[-n:]

    def best(self, direction: str) -> Optional[ExperimentRecord]:
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
        """Parse records from TSV string content (e.g., from git show)."""
        return _parse_records(content)

    def summary(self) -> dict[str, int]:
        """Return counts by status category (excludes baseline)."""
        records = [r for r in self.read_all() if r.status != "baseline"]
        return {
            "total": len(records),
            "kept": sum(1 for r in records if r.status == "keep"),
            "discarded": sum(1 for r in records if r.status == "discard"),
            "crashed": sum(1 for r in records if r.status == "crash"),
        }
