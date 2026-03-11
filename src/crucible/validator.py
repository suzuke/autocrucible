"""Project validation for crucible experiments."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List

from crucible.config import ConfigError, load_config
from crucible.runner import ExperimentRunner


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str


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
