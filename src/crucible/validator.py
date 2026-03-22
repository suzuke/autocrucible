"""Project validation for crucible experiments."""

from __future__ import annotations

import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from crucible.config import ConfigError, load_config
from crucible.i18n import _
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
        results.append(CheckResult(_("Config"), True, _("config.yaml is valid")))
    except ConfigError as e:
        results.append(CheckResult(_("Config"), False, str(e)))
        return results  # can't continue without config

    # Check Docker availability if sandbox configured
    if config.sandbox and config.sandbox.backend == "docker":
        from crucible.sandbox import check_docker_available
        if check_docker_available():
            results.append(CheckResult(_("Docker"), True, _("Docker daemon is available")))
        else:
            results.append(CheckResult(_("Docker"), False,
                _("Docker not available but sandbox.backend is 'docker'. "
                  "Install Docker or set sandbox.backend to 'none'")))

    # 2. Instructions file exists
    instructions_name = config.agent.instructions or "program.md"
    crucible_path = project_root / ".crucible" / instructions_name
    root_path = project_root / instructions_name
    if crucible_path.exists() and crucible_path.read_text().strip():
        results.append(CheckResult(_("Instructions"), True, _("{path} exists").format(path=crucible_path)))
    elif root_path.exists() and root_path.read_text().strip():
        results.append(CheckResult(_("Instructions"), True, _("{path} exists").format(path=root_path)))
    else:
        results.append(CheckResult(_("Instructions"), False, _("{name} not found or empty").format(name=instructions_name)))

    # 3. Editable/readonly files exist
    all_ok = True
    for f in config.files.editable:
        if not (project_root / f).exists():
            results.append(CheckResult(_("Editable files"), False, _("Missing: {f}").format(f=f)))
            all_ok = False
    for f in config.files.readonly:
        if not (project_root / f).exists():
            results.append(CheckResult(_("Readonly files"), False, _("Missing: {f}").format(f=f)))
            all_ok = False
    for f in config.files.hidden:
        if not (project_root / f).exists():
            results.append(CheckResult(_("Hidden files"), False, _("Missing: {f}").format(f=f)))
            all_ok = False
    if all_ok:
        results.append(CheckResult(_("Editable files"), True, _("All files exist")))

    # Check artifacts don't overlap with other file categories
    if config.files.artifacts:
        other_files = set(config.files.editable + config.files.readonly + config.files.hidden)
        overlap = set(config.files.artifacts) & other_files
        if overlap:
            results.append(CheckResult(
                _("Artifacts"), False,
                _("Artifacts overlap with other file categories: {overlap}").format(overlap=", ".join(sorted(overlap)))
            ))
        else:
            results.append(CheckResult(_("Artifacts"), True,
                _("Persistent dirs: {dirs}").format(dirs=", ".join(config.files.artifacts))))

    # 4. Run command executes
    runner = ExperimentRunner(workspace=project_root)
    validate_timeout = min(config.constraints.timeout_seconds, 120)
    run_result = runner.execute(config.commands.run, timeout=validate_timeout)
    if run_result.exit_code == 0 and not run_result.timed_out:
        results.append(CheckResult(_("Run command"), True, _("Executed successfully")))
    elif run_result.timed_out:
        results.append(CheckResult(_("Run command"), False, _("Timed out ({timeout}s)").format(timeout=validate_timeout)))
    else:
        results.append(CheckResult(_("Run command"), False, _("Exit code {code}").format(code=run_result.exit_code)))

    # 5. Eval command parses metric
    metric_value = runner.parse_metric(config.commands.eval, config.metric.name)
    if metric_value is not None:
        results.append(CheckResult(_("Eval/metric"), True, f"{config.metric.name}: {metric_value}"))
    else:
        results.append(CheckResult(_("Eval/metric"), False, _("Could not parse '{name}' from eval output").format(name=config.metric.name)))

    # 6. Stability check (auto-fixes evaluation.repeat if unstable)
    stability = run_stability_check_and_update(project_root, config, runs=3)
    if stability.reason == "repeat already configured":
        results.append(CheckResult(
            _("Stability"), True,
            _("evaluation.repeat={repeat} already configured — skipping check").format(repeat=config.evaluation.repeat)
        ))
    elif stability.stable:
        results.append(CheckResult(
            _("Stability"), True,
            _("CV={cv:.1f}%  mean={mean:.4f}  stdev={stdev:.4f} ✓ stable").format(
                cv=stability.cv, mean=stability.mean, stdev=stability.stdev)
        ))
    else:
        results.append(CheckResult(
            _("Stability"), True,
            _("CV={cv:.1f}% ⚠ unstable — auto-set evaluation.repeat=3 in config.yaml").format(cv=stability.cv)
        ))

    return results


def run_stability_check_and_update(
    project_root: Path, config, runs: int = 3
) -> StabilityResult:
    """Run stability check and auto-update config.yaml if metric is unstable.

    If evaluation.repeat is already > 1, skip and return stable.
    Writes .crucible/.validated marker on completion.
    """
    if config.evaluation.repeat > 1:
        return StabilityResult(stable=True, reason="repeat already configured")

    result = check_stability(project_root, config, runs=runs)

    if not result.stable:
        import yaml as _yaml
        config_path = project_root / ".crucible" / "config.yaml"
        with open(config_path) as f:
            raw = _yaml.safe_load(f)
        raw.setdefault("evaluation", {})["repeat"] = 3
        raw["evaluation"].setdefault("aggregation", "median")
        with open(config_path, "w") as f:
            _yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)

    marker = project_root / ".crucible" / ".validated"
    marker.write_text("")

    return result


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
