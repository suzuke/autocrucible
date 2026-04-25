"""SmolagentsBackend — M2 PR 13.

Production-grade adapter wrapping smolagents `ToolCallingAgent` +
`LiteLLMModel`. Implements `crucible.agents.base.AgentInterface` so it
plugs into the existing Orchestrator without conditional code paths.

Design constraints (reviewer round 1):
  - smolagents / litellm are OPTIONAL dependencies. Imported lazily so
    `crucible.agents` and `load_config` don't pull them in.
  - `ToolCallingAgent` is the ONLY supported mode in default safe (per
    spec §INV-3). CodeAct is intentionally NOT exposed via config.
  - Tool registry is fixed: `read_file / write_file / edit_file / glob
    / grep`. Forbidden tools are absent by construction.
  - API key VALUE is never stored in the backend; only the env var
    NAME is referenced. LiteLLMModel reads the env at request time.
  - `backend_kind` is the stable string `"smolagents"`.
  - `backend_version` is best-effort: missing version → `"unknown"`.
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from crucible.agents.base import AgentErrorType, AgentInterface, AgentResult

if TYPE_CHECKING:
    from crucible.config import SmolagentsConfig
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy


logger = logging.getLogger(__name__)


# Stable identifier embedded in AttemptNode.backend_kind. Must NOT
# encode provider/model — those belong in metadata fields elsewhere.
BACKEND_KIND = "smolagents"


class SmolagentsImportError(ImportError):
    """Raised when smolagents/litellm are not installed but requested.

    Message includes the actionable install command so users don't
    have to guess the extra name.
    """

    def __init__(self, missing_module: str) -> None:
        super().__init__(
            f"smolagents AgentBackend requires the optional `[smolagents]` "
            f"extra. Missing: {missing_module}.\n"
            f"  Install with: pip install 'autocrucible[smolagents]'"
        )
        self.missing_module = missing_module


def _resolve_backend_version() -> str:
    """Best-effort backend version. Reviewer round 1 Q5.

    Try `importlib.metadata.version("smolagents")` first; fall back to
    the package's `__version__` attribute. Return "unknown" if both
    fail — never raise.
    """
    try:
        return importlib.metadata.version("smolagents")
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        import smolagents
        return getattr(smolagents, "__version__", "unknown") or "unknown"
    except Exception:
        return "unknown"


def _import_smolagents():
    """Lazy import. Raises SmolagentsImportError with install hint."""
    try:
        import smolagents  # noqa: F401
    except ImportError as exc:
        raise SmolagentsImportError("smolagents") from exc
    try:
        import litellm  # noqa: F401
    except ImportError as exc:
        raise SmolagentsImportError("litellm") from exc


class SmolagentsBackend(AgentInterface):
    """Default-safe-mode smolagents backend.

    Always uses `ToolCallingAgent` with the fixed 5-tool registry from
    `_smolagents_tools.DEFAULT_SAFE_TOOLS`. Per spec §INV-3, no
    `run_python` / `run_shell` / `eval_code` / `execute` / CodeAct
    executor — those are absent by construction.

    Construction is pure: imports the framework, builds the model and
    tools, but does NOT make a network call until `generate_edit()` is
    invoked.
    """

    def __init__(
        self,
        *,
        config: "SmolagentsConfig",
        policy: "CheatResistancePolicy",
        workspace: Path,
        system_prompt: str | None = None,
    ) -> None:
        _import_smolagents()  # lazy: raises with install hint if missing
        from smolagents import LiteLLMModel, ToolCallingAgent

        from crucible.agents._smolagents_tools import build_default_tools

        self._config = config
        self._policy = policy
        self._workspace = workspace
        self._system_prompt = system_prompt
        self._backend_version = _resolve_backend_version()

        # API key: read env var name from config; LiteLLM reads VALUE
        # at request time. Never store the value on this object.
        self._api_key_env = config.api_key_env

        # Surface a clear error early if the env is missing — but don't
        # block construction (POC patterns may set it later).
        if not os.environ.get(self._api_key_env):
            logger.warning(
                "smolagents backend: env var %s is not set — model calls "
                "will fail at request time unless set before generate_edit().",
                self._api_key_env,
            )

        # Provider/model are forwarded verbatim to LiteLLM. The provider
        # prefix tells LiteLLM which backend to route to (anthropic/,
        # openai/, openrouter/, etc.).
        model_id = (
            config.model
            if "/" in config.model
            else f"{config.provider}/{config.model}"
        )
        self._model = LiteLLMModel(
            model_id=model_id,
            api_key=None,  # let LiteLLM read from env at request time
        )

        self._tools = build_default_tools(policy=policy, workspace=workspace)
        self._agent = ToolCallingAgent(
            tools=self._tools,
            model=self._model,
            max_steps=config.max_steps,
        )

    # ------------------------------------------------------------------
    # AgentInterface implementation
    # ------------------------------------------------------------------

    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        """Run one edit attempt. The `workspace` arg MUST match the one
        the backend was constructed with; mismatch is a hard error
        (reviewer round 2 F1).

        The backend owns its tools and CheatResistancePolicy bound to a
        specific workspace; running with a different workspace path
        would silently apply the wrong ACL and read/write the wrong
        files. Programmatic callers that legitimately need to switch
        workspaces should construct a new SmolagentsBackend.
        """
        if workspace.resolve() != self._workspace.resolve():
            raise ValueError(
                f"smolagents backend was constructed for workspace "
                f"{self._workspace} but generate_edit was called with "
                f"{workspace}. The backend's tools and policy are bound "
                f"to the construction workspace; create a new backend "
                f"instance for a different workspace."
            )

        full_prompt = self._compose_prompt(prompt)
        snapshot_before = _snapshot_mtimes(self._workspace)
        t0 = time.monotonic()

        try:
            result = self._agent.run(full_prompt, max_steps=self._config.max_steps)
            description = str(result) if result is not None else ""
            error_type = None
            agent_output = description
        except Exception as exc:
            description = ""
            agent_output = f"smolagents agent error: {exc}"
            error_type = _classify_error(str(exc))
            logger.warning("smolagents backend: agent error: %s", exc)

        duration = time.monotonic() - t0
        modified = _diff_mtimes(self._workspace, snapshot_before)

        # Usage is provider-dependent — LiteLLM exposes some via
        # response objects, but the smolagents wrapper doesn't surface
        # cost in a stable way as of v1.24. Punt for v1: pass None
        # so the orchestrator doesn't show misleading zeros.
        usage = None

        return AgentResult(
            modified_files=modified,
            description=description,
            usage=usage,
            duration_seconds=duration,
            agent_output=agent_output,
            error_type=error_type,
        )

    def capabilities(self) -> set[str]:
        # Mirrors the tool registry. Used by the orchestrator for
        # capability-gated features. No `run_python` / `run_shell` etc.
        return {"read", "edit", "write", "glob", "grep"}

    # ------------------------------------------------------------------
    # Identity (recorded on AttemptNode by the orchestrator)
    # ------------------------------------------------------------------

    @property
    def backend_kind(self) -> str:
        return BACKEND_KIND

    @property
    def backend_version(self) -> str:
        return self._backend_version

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compose_prompt(self, user_prompt: str) -> str:
        """Prepend system_prompt if set; otherwise return user_prompt as-is.

        ToolCallingAgent supports a system prompt via `system_prompt=`
        on construction in newer smolagents, but pinning to v1.24 we
        prepend it inline for stability.
        """
        if not self._system_prompt:
            return user_prompt
        return f"{self._system_prompt}\n\n---\n\n{user_prompt}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_AUTH_PATTERNS = frozenset(
    {"unauthorized", "authentication", "invalid api key", "api key"}
)


def _classify_error(msg: str) -> AgentErrorType:
    lower = msg.lower()
    if any(p in lower for p in _AUTH_PATTERNS):
        return AgentErrorType.AUTH
    if "timeout" in lower:
        return AgentErrorType.TIMEOUT
    return AgentErrorType.UNKNOWN


def _snapshot_mtimes(workspace: Path) -> dict[Path, float]:
    """Take a {abs_path: mtime} snapshot of all files in workspace."""
    snapshot: dict[Path, float] = {}
    for p in workspace.rglob("*"):
        if p.is_file():
            try:
                snapshot[p.resolve()] = p.stat().st_mtime
            except OSError:
                continue
    return snapshot


def _diff_mtimes(
    workspace: Path, before: dict[Path, float]
) -> list[Path]:
    """Return files whose mtime changed (or appeared) since `before`."""
    changed: list[Path] = []
    for p in workspace.rglob("*"):
        if not p.is_file():
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        absolute = p.resolve()
        if absolute not in before or before[absolute] != mt:
            changed.append(p)
    return changed
