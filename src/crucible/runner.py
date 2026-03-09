"""Experiment runner with subprocess execution, timeout, and metric parsing."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class RunResult:
    """Result of a command execution."""

    exit_code: int
    timed_out: bool
    stderr_tail: str = ""


class ExperimentRunner:
    """Runs experiment commands with timeout and metric parsing."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def execute(self, command: str, timeout: int) -> RunResult:
        """Run a shell command with a timeout.

        Returns a RunResult with exit code, timeout flag, and last 50 lines of stderr.
        """
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=self.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            stderr_tail = _tail(stderr, 50)
            return RunResult(
                exit_code=proc.returncode,
                timed_out=False,
                stderr_tail=stderr_tail,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()
            return RunResult(
                exit_code=-1,
                timed_out=True,
                stderr_tail=_tail(stderr, 50),
            )
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
            raise

    def parse_metric(self, eval_command: str, metric_name: str) -> Optional[float]:
        """Run an eval command and parse a named metric from its output.

        Looks for lines matching ``<metric_name>: <value>`` and returns the
        value as a float, or None if not found.
        """
        try:
            proc = subprocess.run(
                eval_command,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            pattern = re.compile(rf"^{re.escape(metric_name)}:\s*(.+)$", re.MULTILINE)
            match = pattern.search(proc.stdout)
            if match:
                return float(match.group(1).strip())
        except (subprocess.TimeoutExpired, ValueError):
            pass
        return None


def _tail(text: str, n: int) -> str:
    """Return the last *n* lines of *text*."""
    lines = text.splitlines()
    return "\n".join(lines[-n:])
