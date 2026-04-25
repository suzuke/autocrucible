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
class SmolagentsConfig:
    """M2 PR 13: configuration for the smolagents AgentBackend.

    Per spec §INV-3: only ToolCallingAgent is supported in default safe
    mode. CodeAct mode is intentionally NOT exposed via config in PR 13.
    """
    # LiteLLM provider routing: "anthropic", "openai", "openrouter", etc.
    # Passed verbatim to smolagents.LiteLLMModel.
    provider: str = "anthropic"
    # Model id forwarded to LiteLLM (e.g. "claude-3-5-sonnet-20241022").
    model: str = "claude-3-5-sonnet-20241022"
    # Name of the env var to read the API key FROM. The value itself is
    # never stored in config / logs / prompts (reviewer round 1 Q2).
    api_key_env: str = "ANTHROPIC_API_KEY"
    # Hard cap on agent.run() steps; prevents runaway tool-use loops.
    max_steps: int = 12


@dataclass
class AgentConfig:
    type: str = "claude-code"          # "claude-code" | "smolagents"
    instructions: Optional[str] = None
    system_prompt: Optional[str] = None
    model: str | None = None
    language: str | None = None
    base_url: str | None = None
    failure_analysis: bool = False
    critic: CriticConfig = field(default_factory=CriticConfig)
    context_window: ContextWindowConfig = field(default_factory=ContextWindowConfig)
    smolagents: SmolagentsConfig = field(default_factory=SmolagentsConfig)


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
    # M2 PR 10: doom-loop pruning threshold for BFTSLiteStrategy.
    # When a kept node has accumulated this many consecutive trailing
    # failed-expansion children (discard / crash / non-improving keep),
    # BFTS stops considering it for BranchFrom and falls back to the
    # next-best kept node. Other strategies ignore this field.
    prune_threshold: int = 3


@dataclass
class SealConfig:
    """M2 PR 12: integrity / authenticity policy for `eval-result.json` seals.

    M1 used `content-sha256:<hex>` (corruption check only). M2 introduces
    `hmac-sha256:<key-id>:<hex>` (tamper-evidence under Docker isolation,
    where the agent has no access to the host's CRUCIBLE_SEAL_KEY).

    Default `algorithm: content-sha256` so existing projects keep their
    M1 byte-identical seal output. Users opt into HMAC explicitly.
    """
    algorithm: str = "content-sha256"   # "content-sha256" | "hmac-sha256"
    key_id: str = "default"             # opaque label; embedded in seal string
    key_env_var: str = "CRUCIBLE_SEAL_KEY"  # hex-encoded bytes
    key_file: str | None = None         # path to a hex-encoded keyfile; takes
                                        # precedence over key_env_var if set


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
    seal: SealConfig = field(default_factory=SealConfig)


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


_SUPPORTED_AGENT_TYPES = ("claude-code", "smolagents")


def _build_smolagents(data: dict) -> SmolagentsConfig:
    if not data:
        return SmolagentsConfig()
    max_steps = data.get("max_steps", 12)
    if not isinstance(max_steps, int) or max_steps < 1:
        raise ConfigError(
            f"agent.smolagents.max_steps must be a positive int, got {max_steps!r}"
        )
    return SmolagentsConfig(
        provider=data.get("provider", "anthropic"),
        model=data.get("model", "claude-3-5-sonnet-20241022"),
        api_key_env=data.get("api_key_env", "ANTHROPIC_API_KEY"),
        max_steps=max_steps,
    )


def _build_agent(data: dict) -> AgentConfig:
    if not data:
        return AgentConfig()
    agent_type = data.get("type", "claude-code")
    if agent_type not in _SUPPORTED_AGENT_TYPES:
        raise ConfigError(
            f"agent.type must be one of {list(_SUPPORTED_AGENT_TYPES)}, "
            f"got {agent_type!r}"
        )
    return AgentConfig(
        type=agent_type,
        instructions=data.get("instructions"),
        system_prompt=data.get("system_prompt"),
        model=data.get("model"),
        base_url=data.get("base_url"),
        language=data.get("language"),
        failure_analysis=data.get("failure_analysis", False),
        critic=_build_critic(data.get("critic")),
        context_window=_build_context_window(data.get("context_window", {})),
        smolagents=_build_smolagents(data.get("smolagents", {})),
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
    prune_threshold = search_data.get("prune_threshold", 3)
    if not isinstance(prune_threshold, int) or prune_threshold < 1:
        raise ConfigError(
            f"search.prune_threshold must be a positive int, got {prune_threshold!r}"
        )
    return SearchConfig(
        strategy=search_data.get("strategy", "greedy"),
        beam_width=search_data.get("beam_width", 3),
        plateau_threshold=plateau,
        prune_threshold=prune_threshold,
    )


_SUPPORTED_SEAL_ALGORITHMS = ("content-sha256", "hmac-sha256")


def _build_seal(seal_data: dict) -> SealConfig:
    if not seal_data:
        return SealConfig()
    algorithm = seal_data.get("algorithm", "content-sha256")
    if algorithm not in _SUPPORTED_SEAL_ALGORITHMS:
        raise ConfigError(
            f"seal.algorithm must be one of {list(_SUPPORTED_SEAL_ALGORITHMS)}, "
            f"got {algorithm!r}"
        )
    key_id = seal_data.get("key_id", "default")
    if not isinstance(key_id, str) or not key_id:
        raise ConfigError("seal.key_id must be a non-empty string")
    # M2 PR 12 reviewer: key_id MUST NOT contain ':' since it terminates
    # the algorithm/key_id portion of the seal string.
    if ":" in key_id:
        raise ConfigError(
            f"seal.key_id must not contain ':' (got {key_id!r}); "
            f"the seal format is '<algorithm>:<key-id>:<hex>'"
        )
    key_env_var = seal_data.get("key_env_var", "CRUCIBLE_SEAL_KEY")
    key_file = seal_data.get("key_file")
    return SealConfig(
        algorithm=algorithm,
        key_id=key_id,
        key_env_var=key_env_var,
        key_file=key_file,
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
        seal=_build_seal(raw.get("seal", {})),
    )
