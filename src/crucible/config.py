"""Configuration loading and validation for crucible projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class ContextWindowConfig:
    include_history: bool = True
    history_limit: int = 20
    include_best: bool = True


@dataclass
class CriticConfig:
    enabled: bool = False
    model: str = "haiku"


@dataclass
class AgentConfig:
    type: str = "claude-code"
    instructions: Optional[str] = None
    system_prompt: Optional[str] = None
    model: str | None = None
    language: str | None = None
    base_url: str | None = None
    failure_analysis: bool = False
    critic: CriticConfig = field(default_factory=CriticConfig)
    context_window: ContextWindowConfig = field(default_factory=ContextWindowConfig)


@dataclass
class FilesConfig:
    editable: List[str] = field(default_factory=list)
    readonly: List[str] = field(default_factory=list)
    hidden: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)


@dataclass
class CommandsConfig:
    run: str = ""
    eval: str = ""
    setup: Optional[str] = None


@dataclass
class MetricConfig:
    name: str = ""
    direction: str = ""


@dataclass
class BudgetConfig:
    max_cost_usd: float | None = None
    max_cost_per_iter_usd: float | None = None
    warn_at_percent: int = 80


@dataclass
class ConstraintsConfig:
    timeout_seconds: int = 600
    max_retries: int = 3
    budget: BudgetConfig | None = None
    plateau_threshold: int = 8
    allow_install: bool = False
    max_iterations: int | None = None
    convergence_window: int | None = None  # stop after N iters with no improvement
    min_improvement: float | None = None   # relative threshold (0.001 = 0.1%)


@dataclass
class GitConfig:
    branch_prefix: str = "crucible"
    tag_failed: bool = True


@dataclass
class EvaluationConfig:
    repeat: int = 1
    aggregation: str = "median"  # median | mean


@dataclass
class SandboxConfig:
    backend: str = "none"  # docker | none
    base_image: str = "python:3.12-slim"
    network: bool = False
    memory_limit: str | None = None  # e.g. "2g"
    cpu_limit: int | None = None


@dataclass
class SearchConfig:
    strategy: str = "greedy"   # greedy | restart | beam | bfts-lite
    beam_width: int = 3
    plateau_threshold: int = 8


@dataclass
class Config:
    name: str = ""
    description: str = ""
    files: FilesConfig = field(default_factory=FilesConfig)
    commands: CommandsConfig = field(default_factory=CommandsConfig)
    metric: MetricConfig = field(default_factory=MetricConfig)
    constraints: ConstraintsConfig = field(default_factory=ConstraintsConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    git: GitConfig = field(default_factory=GitConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    sandbox: SandboxConfig | None = None
    search: SearchConfig = field(default_factory=SearchConfig)


def _build_context_window(data: dict) -> ContextWindowConfig:
    if not data:
        return ContextWindowConfig()
    return ContextWindowConfig(
        include_history=data.get("include_history", True),
        history_limit=data.get("history_limit", 20),
        include_best=data.get("include_best", True),
    )


def _build_budget(data: dict | None) -> BudgetConfig | None:
    if not data:
        return None
    return BudgetConfig(
        max_cost_usd=data.get("max_cost_usd"),
        max_cost_per_iter_usd=data.get("max_cost_per_iter_usd"),
        warn_at_percent=data.get("warn_at_percent", 80),
    )


def _build_critic(data: dict | None) -> CriticConfig:
    if not data:
        return CriticConfig()
    return CriticConfig(
        enabled=data.get("enabled", False),
        model=data.get("model", "haiku"),
    )


def _build_agent(data: dict) -> AgentConfig:
    if not data:
        return AgentConfig()
    return AgentConfig(
        type=data.get("type", "claude-code"),
        instructions=data.get("instructions"),
        system_prompt=data.get("system_prompt"),
        model=data.get("model"),
        base_url=data.get("base_url"),
        language=data.get("language"),
        failure_analysis=data.get("failure_analysis", False),
        critic=_build_critic(data.get("critic")),
        context_window=_build_context_window(data.get("context_window", {})),
    )


def _build_evaluation(data: dict) -> EvaluationConfig:
    if not data:
        return EvaluationConfig()
    return EvaluationConfig(
        repeat=data.get("repeat", 1),
        aggregation=data.get("aggregation", "median"),
    )


def _build_sandbox(data: dict | None) -> SandboxConfig | None:
    if not data:
        return None
    return SandboxConfig(
        backend=data.get("backend", "none"),
        base_image=data.get("base_image", "python:3.12-slim"),
        network=data.get("network", False),
        memory_limit=data.get("memory_limit"),
        cpu_limit=data.get("cpu_limit"),
    )


def _build_search(search_data: dict, constraints_data: dict) -> SearchConfig:
    plateau = search_data.get(
        "plateau_threshold",
        constraints_data.get("plateau_threshold", 8),
    )
    return SearchConfig(
        strategy=search_data.get("strategy", "greedy"),
        beam_width=search_data.get("beam_width", 3),
        plateau_threshold=plateau,
    )


def _require(raw: dict, *keys: str) -> None:
    """Validate that dotted key paths are present and non-empty."""
    for key in keys:
        parts = key.split(".")
        obj = raw
        for part in parts:
            if not isinstance(obj, dict) or part not in obj:
                raise ConfigError(f"Required field '{key}' is missing")
            obj = obj[part]
        if obj is None or obj == "" or obj == []:
            raise ConfigError(f"Required field '{key}' is empty")


def load_config(project_root: Path) -> Config:
    """Load and validate .crucible/config.yaml from *project_root*."""
    config_path = project_root / ".crucible" / "config.yaml"
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigError("Config file is not a valid YAML mapping")

    _require(
        raw,
        "name",
        "files.editable",
        "commands.run",
        "commands.eval",
        "metric.name",
        "metric.direction",
    )

    direction = raw["metric"]["direction"]
    if direction not in ("minimize", "maximize"):
        raise ConfigError(f"metric.direction must be 'minimize' or 'maximize', got '{direction}'")

    search_data = raw.get("search", {})
    strategy = search_data.get("strategy", "greedy")
    if strategy not in ("greedy", "restart", "beam", "bfts-lite"):
        raise ConfigError(
            f"search.strategy must be 'greedy', 'restart', 'beam', or 'bfts-lite', "
            f"got '{strategy}'"
        )

    files_data = raw.get("files", {})
    commands_data = raw.get("commands", {})
    metric_data = raw.get("metric", {})
    constraints_data = raw.get("constraints", {})
    git_data = raw.get("git", {})

    return Config(
        name=raw["name"],
        description=raw.get("description", ""),
        files=FilesConfig(
            editable=files_data.get("editable", []),
            readonly=files_data.get("readonly", []),
            hidden=files_data.get("hidden", []),
            artifacts=files_data.get("artifacts", []),
        ),
        commands=CommandsConfig(
            run=commands_data["run"],
            eval=commands_data["eval"],
            setup=commands_data.get("setup"),
        ),
        metric=MetricConfig(
            name=metric_data["name"],
            direction=metric_data["direction"],
        ),
        constraints=ConstraintsConfig(
            timeout_seconds=constraints_data.get("timeout_seconds", 600),
            max_retries=constraints_data.get("max_retries", 3),
            budget=_build_budget(constraints_data.get("budget")),
            plateau_threshold=constraints_data.get("plateau_threshold", 8),
            allow_install=constraints_data.get("allow_install", False),
            max_iterations=constraints_data.get("max_iterations"),
            convergence_window=constraints_data.get("convergence_window"),
            min_improvement=constraints_data.get("min_improvement"),
        ),
        agent=_build_agent(raw.get("agent", {})),
        git=GitConfig(
            branch_prefix=git_data.get("branch_prefix", "crucible"),
            tag_failed=git_data.get("tag_failed", True),
        ),
        evaluation=_build_evaluation(raw.get("evaluation", {})),
        sandbox=_build_sandbox(raw.get("sandbox")),
        search=_build_search(
            raw.get("search", {}),
            raw.get("constraints", {}),
        ),
    )
