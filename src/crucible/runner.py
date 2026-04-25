"""Experiment runner with subprocess execution, timeout, and metric parsing."""

from __future__ import annotations

import os
import re
import signal
import statistics
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
    stdout: str = ""  # M1b: captured for SandboxRunner.parse_metric (Docker mode)


class ExperimentRunner:
    """Runs experiment commands with timeout and metric parsing."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def _make_env(self) -> dict[str, str]:
        """Build env dict that activates the project's .venv if present."""
        env = os.environ.copy()
        venv_bin = self.workspace / ".venv" / "bin"
        if venv_bin.is_dir():
            env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
            env["VIRTUAL_ENV"] = str(self.workspace / ".venv")
        return env

    def execute(self, command: str, timeout: int) -> RunResult:
        """Run a shell command with a timeout.

        Returns a RunResult with exit code, timeout flag, and last 50 lines of stderr.
        If the project has a .venv, its bin/ is prepended to PATH so that
        ``python3`` resolves to the project's interpreter.
        """
        env = self._make_env()
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=self.workspace,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
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
            # Kill entire process group (shell + all children)
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                proc.terminate()
            try:
                _, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
                _, stderr = proc.communicate()
            return RunResult(
                exit_code=-1,
                timed_out=True,
                stderr_tail=_tail(stderr, 50),
            )
        except KeyboardInterrupt:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait()
            raise

    def execute_with_repeat(
        self,
        run_cmd: str,
        eval_cmd: str,
        metric_name: str,
        repeat: int,
        aggregation: str,
        timeout: int,
    ) -> tuple[RunResult, float | None]:
        """Execute experiment repeat times, return aggregated metric."""
        return run_with_repeat(self, run_cmd, eval_cmd, metric_name, repeat, aggregation, timeout)

    def parse_metric(self, eval_command: str, metric_name: str, timeout: int = 30) -> Optional[float]:
        """Run an eval command and parse a named metric from its output.

        Looks for lines matching ``<metric_name>: <value>`` and returns the
        value as a float, or None if not found.
        """
        try:
            proc = subprocess.run(
                eval_command,
                shell=True,
                cwd=self.workspace,
                env=self._make_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            pattern = re.compile(rf"^{re.escape(metric_name)}:\s*(.+)$", re.MULTILINE)
            match = pattern.search(proc.stdout)
            if match:
                return float(match.group(1).strip())
        except (subprocess.TimeoutExpired, ValueError):
            pass
        return None


def run_with_repeat(
    runner,
    run_cmd: str,
    eval_cmd: str,
    metric_name: str,
    repeat: int,
    aggregation: str,
    timeout: int,
) -> tuple[RunResult, float | None]:
    """Execute experiment repeat times using any runner with execute/parse_metric."""
    values: list[float] = []
    last_result = None
    for _ in range(repeat):
        result = runner.execute(run_cmd, timeout)
        last_result = result
        if result.exit_code != 0 or result.timed_out:
            return result, None
        metric = runner.parse_metric(eval_cmd, metric_name)
        if metric is None:
            return result, None
        values.append(metric)
    if not values:
        return last_result, None
    if aggregation == "mean":
        return last_result, statistics.mean(values)
    return last_result, statistics.median(values)


def _tail(text: str, n: int) -> str:
    """Return the last *n* lines of *text*."""
    lines = text.splitlines()
    return "\n".join(lines[-n:])
