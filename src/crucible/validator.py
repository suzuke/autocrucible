"""Project validation for crucible experiments."""

from __future__ import annotations

import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from crucible.config import ConfigError, load_config
from crucible.runner import ExperimentRunner


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str


@dataclass
class StabilityResult:
    stable: bool
    cv: float = 0.0
    values: list[float] | None = None
    mean: float = 0.0
    stdev: float = 0.0
    reason: str = ""


def validate_project(project_root: Path) -> List[CheckResult]:
    """Run all validation checks and return results."""
    results: List[CheckResult] = []

    # 1. Config syntax + required fields
    try:
        config = load_config(project_root)
        results.append(CheckResult("Config", True, "config.yaml is valid"))
    except ConfigError as e:
        results.append(CheckResult("Config", False, str(e)))
        return results  # can't continue without config

    # Check Docker availability if sandbox configured
    if config.sandbox and config.sandbox.backend == "docker":
        from crucible.sandbox import check_docker_available
        if check_docker_available():
            results.append(CheckResult("Docker", True, "Docker daemon is available"))
        else:
            results.append(CheckResult("Docker", False,
                "Docker not available but sandbox.backend is 'docker'. "
                "Install Docker or set sandbox.backend to 'none'"))

    # 2. Instructions file exists
    instructions_name = config.agent.instructions or "program.md"
    crucible_path = project_root / ".crucible" / instructions_name
    root_path = project_root / instructions_name
    if crucible_path.exists() and crucible_path.read_text().strip():
        results.append(CheckResult("Instructions", True, f"{crucible_path} exists"))
    elif root_path.exists() and root_path.read_text().strip():
        results.append(CheckResult("Instructions", True, f"{root_path} exists"))
    else:
        results.append(CheckResult("Instructions", False, f"{instructions_name} not found or empty"))

    # 3. Editable/readonly files exist
    all_ok = True
    for f in config.files.editable:
        if not (project_root / f).exists():
            results.append(CheckResult("Editable files", False, f"Missing: {f}"))
            all_ok = False
    for f in config.files.readonly:
        if not (project_root / f).exists():
            results.append(CheckResult("Readonly files", False, f"Missing: {f}"))
            all_ok = False
    for f in config.files.hidden:
        if not (project_root / f).exists():
            results.append(CheckResult("Hidden files", False, f"Missing: {f}"))
            all_ok = False
    if all_ok:
        results.append(CheckResult("Editable files", True, "All files exist"))

    # 4. Run command executes
    runner = ExperimentRunner(workspace=project_root)
    validate_timeout = min(config.constraints.timeout_seconds, 120)
    run_result = runner.execute(config.commands.run, timeout=validate_timeout)
    if run_result.exit_code == 0 and not run_result.timed_out:
        results.append(CheckResult("Run command", True, "Executed successfully"))
    elif run_result.timed_out:
        results.append(CheckResult("Run command", False, f"Timed out ({validate_timeout}s)"))
    else:
        results.append(CheckResult("Run command", False, f"Exit code {run_result.exit_code}"))

    # 5. Eval command parses metric
    metric_value = runner.parse_metric(config.commands.eval, config.metric.name)
    if metric_value is not None:
        results.append(CheckResult("Eval/metric", True, f"{config.metric.name}: {metric_value}"))
    else:
        results.append(CheckResult("Eval/metric", False, f"Could not parse '{config.metric.name}' from eval output"))

    return results


def check_stability(workspace: Path, config, runs: int = 5) -> StabilityResult:
    """Run experiment N times and compute coefficient of variation."""
    runner = ExperimentRunner(workspace=workspace)
    timeout = min(config.constraints.timeout_seconds, 120)
    values: list[float] = []
    for _ in range(runs):
        run_result = runner.execute(config.commands.run, timeout)
        if run_result.exit_code != 0 or run_result.timed_out:
            continue
        metric = runner.parse_metric(config.commands.eval, config.metric.name)
        if metric is not None:
            values.append(metric)

    if len(values) < 2:
        return StabilityResult(stable=False, reason="too few successful runs")

    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    cv = (stdev / abs(mean) * 100) if mean != 0 else float("inf")

    return StabilityResult(
        stable=cv < 5.0,
        cv=cv, values=values, mean=mean, stdev=stdev,
    )
