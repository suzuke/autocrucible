"""SubscriptionCLIBackend — top-level wrapper (M3 PR 16).

Implements `AgentInterface` over a `SubscriptionCLIAdapter`. Owns:

  - **Two-flag opt-in** (reviewer Q8): construct refuses unless config
    sets BOTH `experimental.allow_cli_subscription` AND
    `experimental.acknowledge_unsandboxed_cli`.
  - **Compliance gate enforcement** (reviewer Q2): refuses to operate
    unless a recent ≥99% report exists for the configured adapter +
    `cli_version`. Override via `experimental.allow_stale_compliance`
    with a red-letter WARNING log.
  - **Scratch-dir isolation** (reviewer Q8 reframe): copies declared
    editable + readonly files into a temp dir, runs the CLI there, and
    copies modified editable files back. NOT security — reproducibility.
  - **Tri-state safety detection** (reviewer Q6): every run emits the
    `provider_safety_filter_active` tag for red-team disambiguation.
  - **Isolation tag**: every AttemptNode is tagged
    `isolation="cli_subscription_unsandboxed"` (reviewer Q8 truth-in-
    labeling).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from crucible.agents.base import (
    AgentErrorType,
    AgentInterface,
    AgentResult,
)
from crucible.agents.cli_subscription.base import (
    BACKEND_KIND,
    ISOLATION_TAG,
    AdapterNotImplementedError,
    AdapterRunContext,
    CLIBinaryError,
    SubscriptionCLIAdapter,
)
from crucible.agents.cli_subscription.compliance import (
    RELEASE_THRESHOLD,
    reports_dir_for,
    verify_recent_pass_with_path,
)
from crucible.agents.cli_subscription.redaction import is_secret_env_name
from crucible.agents.cli_subscription.safety import (
    SafetyFilterState,
    detect_safety_filter,
)
from crucible.agents.cli_subscription.scratch import (
    cli_scratch_dir,
    copy_editable_changes_back,
)

if TYPE_CHECKING:
    from crucible.config import (
        CLISubscriptionConfig,
        ExperimentalConfig,
    )
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

logger = logging.getLogger(__name__)


class SubscriptionCLIBackendError(RuntimeError):
    """Construction-time errors (gating, missing flags, missing gate report)."""


class SubscriptionCLIBackend(AgentInterface):
    """Experimental subscription-CLI agent backend.

    See module docstring for the full invariant set. Construction is
    deliberately friction-laden — opt-in flags + compliance gate +
    binary resolution + version snapshot all happen here so misuse
    surfaces at startup, not mid-run.
    """

    def __init__(
        self,
        *,
        cli_config: "CLISubscriptionConfig",
        experimental: "ExperimentalConfig",
        policy: "CheatResistancePolicy",
        workspace: Path,
        project_dir: Optional[Path] = None,
    ) -> None:
        # 1. Two-flag opt-in (reviewer Q8)
        if not experimental.allow_cli_subscription:
            raise SubscriptionCLIBackendError(
                "SubscriptionCLIBackend is EXPERIMENTAL. Set "
                "`experimental.allow_cli_subscription: true` in config "
                "to opt in. See docs/CLI-SUBSCRIPTION-BACKEND.md."
            )
        if not experimental.acknowledge_unsandboxed_cli:
            raise SubscriptionCLIBackendError(
                "SubscriptionCLIBackend runs the CLI UNSANDBOXED on the host "
                "filesystem — Crucible's CheatResistancePolicy ACL does NOT "
                "constrain it (the CLI is a complete agent product per spec "
                "§3.3). Set `experimental.acknowledge_unsandboxed_cli: true` "
                "to confirm you understand the limitation."
            )

        self._cli_config = cli_config
        self._experimental = experimental
        self._policy = policy
        self._workspace = Path(workspace).resolve()
        self._project_dir = (
            Path(project_dir).resolve() if project_dir else self._workspace
        )

        # 2. Resolve adapter + binary + snapshot version
        try:
            self._adapter = self._build_adapter()
        except CLIBinaryError as exc:
            raise SubscriptionCLIBackendError(str(exc)) from exc

        # 3. Compliance gate (reviewer Q2 — enforced not advisory)
        # Reviewer round 2 Bug #2: track BOTH the report and the file
        # path so AttemptNode metadata records the actual report path
        # for audit trail (not the CLI binary path).
        self._compliance_report, self._compliance_report_path = (
            self._check_compliance()
        )

        logger.warning(
            "SubscriptionCLIBackend started: adapter=%s cli=%s version=%s "
            "isolation=%s — CLI runs unsandboxed; ACL does NOT apply.",
            self._adapter.cli_name,
            self._adapter.cli_binary_path,
            self._adapter.cli_version,
            ISOLATION_TAG,
        )

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _build_adapter(self) -> SubscriptionCLIAdapter:
        from crucible.agents.cli_subscription.claude_code_cli import (
            ClaudeCodeCLIAdapter,
        )
        from crucible.agents.cli_subscription.codex_cli import CodexCLIAdapter
        from crucible.agents.cli_subscription.gemini_cli import GeminiCLIAdapter

        # Adapter registry — exhaustive from day 1 even though
        # codex/gemini are stubs (reviewer Q1 "B + stubs" pattern).
        registry: dict[str, type[SubscriptionCLIAdapter]] = {
            "claude-code-cli": ClaudeCodeCLIAdapter,
            "codex-cli": CodexCLIAdapter,
            "gemini-cli": GeminiCLIAdapter,
        }
        cls = registry.get(self._cli_config.adapter)
        if cls is None:
            raise SubscriptionCLIBackendError(
                f"unknown adapter: {self._cli_config.adapter!r}"
            )
        return cls(cli_binary_path=self._cli_config.cli_binary_path)

    def _check_compliance(self):
        """Check the compliance gate. Reviewer round 2 Bug #2: returns
        (report, source_path) so AttemptNode metadata records the report
        file path (audit trail), not the CLI binary path.
        """
        pair = verify_recent_pass_with_path(
            adapter=self._adapter.cli_name,
            cli_binary_path=str(self._adapter.cli_binary_path),
            cli_version=self._adapter.cli_version,
            reports_dir=reports_dir_for(self._project_dir),
            threshold=RELEASE_THRESHOLD,
        )
        if pair is None:
            if not self._experimental.allow_stale_compliance:
                # Phantom-command-free error: reference the real
                # `crucible compliance-check` CLI command (landed in
                # PR 16c) instead of the Python module path. PR 16
                # R2 caught the original phantom-command issue; PR 16c
                # R1 review caught the inverse — pointing users at a
                # module path when the CLI command exists.
                raise SubscriptionCLIBackendError(
                    f"No recent ≥{int(RELEASE_THRESHOLD * 100)}% compliance "
                    f"report found for adapter={self._adapter.cli_name} "
                    f"version={self._adapter.cli_version}. Either run "
                    f"`crucible compliance-check --adapter "
                    f"{self._adapter.cli_name}` to produce a passing report, "
                    f"or set `experimental.allow_stale_compliance: true` to "
                    f"bypass (NOT RECOMMENDED — bypass implies trial results "
                    f"are NOT a containment claim)."
                )
            logger.warning(
                "RED-LETTER: experimental.allow_stale_compliance=true — "
                "running %s without a recent passing compliance report. "
                "Trial results are NOT a containment claim.",
                self._adapter.cli_name,
            )
            return None, None
        return pair  # (report, source_path)

    # ------------------------------------------------------------------
    # AgentInterface
    # ------------------------------------------------------------------

    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        if Path(workspace).resolve() != self._workspace:
            raise ValueError(
                f"SubscriptionCLIBackend was constructed for workspace "
                f"{self._workspace} but generate_edit was called with "
                f"{workspace}. Construct a new backend per workspace."
            )

        # Catch the CLISubscriptionAuthError BASE class so any
        # adapter-specific subclass (CodexCLIAuthError, GeminiCLIAuthError,
        # future) routes to AgentErrorType.AUTH without needing per-
        # adapter except clauses. PR 16b introduced the base class once
        # GeminiCLIAdapter became the second concrete instance; before
        # that the abstraction would have been premature.
        from crucible.agents.cli_subscription.base import (
            CLISubscriptionAuthError,
            ParsedAdapterOutput,
        )

        auth_error_evidence: Optional[str] = None

        with cli_scratch_dir(workspace=self._workspace, policy=self._policy) as scratch:
            ctx = AdapterRunContext(
                prompt=prompt,
                scratch_dir=scratch,
                workspace_root=scratch,  # CLI's cwd is the scratch
                timeout_seconds=self._cli_config.timeout_seconds,
                stdout_cap_bytes=self._cli_config.stdout_cap_bytes,
            )
            raw = self._adapter.run_subprocess(ctx)
            try:
                parsed = self._adapter.parse_output(raw)
            except CLISubscriptionAuthError as exc:
                # Adapter detected a typed auth-failure signal in the
                # event stream. Build a minimal ParsedAdapterOutput so
                # the rest of the metadata path still populates.
                auth_error_evidence = exc.evidence
                parsed = ParsedAdapterOutput(
                    modified_files=[],
                    description=str(exc),
                    structured_events=[],
                    tool_was_called=None,
                    unknown_schema=False,
                )
            modified = copy_editable_changes_back(
                scratch=scratch,
                workspace=self._workspace,
                policy=self._policy,
            )

        # Tri-state safety detection (reviewer Q6)
        safety = detect_safety_filter(
            adapter=self._adapter.cli_name,
            stdout_text=raw.stdout,
            structured_events=parsed.structured_events,
            tool_was_called=parsed.tool_was_called,
        )

        # Error classification — typed exceptions take precedence over
        # exit-code heuristics (PR 16a R1 #4 typed pattern).
        error_type = None
        if auth_error_evidence is not None:
            error_type = AgentErrorType.AUTH
        elif raw.timed_out:
            error_type = AgentErrorType.TIMEOUT
        elif raw.exit_code != 0 or parsed.unknown_schema:
            error_type = AgentErrorType.UNKNOWN

        # Backend metadata propagated to AttemptNode (reviewer
        # spec-conformance #3: extend AgentResult with backend_metadata
        # rather than coupling the adapter to the ledger).
        metadata = {
            "backend_kind": BACKEND_KIND,
            "isolation": ISOLATION_TAG,
            "cli_binary_path": str(self._adapter.cli_binary_path),
            "cli_version": self._adapter.cli_version,
            "cli_argv": raw.argv_redacted,
            "cli_exit_code": raw.exit_code,
            "cli_timed_out": raw.timed_out,
            "cli_stdout_cap_exceeded": raw.stdout_cap_exceeded,
            "provider_safety_filter_active": safety.state.value,
            "provider_safety_filter_source": safety.source,
            "provider_safety_filter_evidence": safety.evidence,
            "unknown_schema_detected": parsed.unknown_schema,
            "tool_was_called": parsed.tool_was_called,
            # Reviewer round 2 Bug #2: actual REPORT FILE path, not CLI
            # binary path. Auditors following this trail will hit the
            # JSONL evidence file, not a binary.
            "compliance_report_path": (
                str(self._compliance_report_path)
                if self._compliance_report_path
                else None
            ),
            # Reviewer round 2 polish: spec §4.1 mandates `env_allowlist`
            # on AttemptNode. CLI subscription mode passes the host env
            # unchanged (CLI tools rely on provider auth env vars), so
            # the "allowlist" is "everything visible to host". We record
            # the NAMES of secret-named env vars we know were passed
            # but redact their values upstream in `redact_env`. This
            # gives operators the audit trail without leaking values.
            "env_allowlist": _record_env_names_seen(),
        }

        return AgentResult(
            modified_files=modified,
            description=parsed.description or raw.stdout[:2000],
            usage=None,  # subscription billing not surfaced
            duration_seconds=raw.duration_seconds,
            agent_output=raw.stdout[:5000],
            error_type=error_type,
            backend_metadata=metadata,
        )

    def capabilities(self) -> set[str]:
        # Reviewer Q8: declare degraded ACL via these capability flags.
        # Callers can branch on them to decide whether to trust the
        # backend's modifications without additional verification.
        return {
            "agent_loop_external",  # CLI runs its own agent loop
            "host_fs_visible",      # ACL does not constrain CLI
        }

    @property
    def backend_kind(self) -> str:
        return BACKEND_KIND

    @property
    def backend_version(self) -> str:
        return self._adapter.cli_version


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_env_names_seen() -> list[str]:
    """Return the list of env var NAMES visible to the CLI subprocess.

    Reviewer round 2 polish: spec §4.1 schema mandates `env_allowlist`
    on every AttemptNode. CLI-subscription backends pass the host env
    unchanged (CLI tools need provider auth env vars), so this is
    effectively "everything in os.environ at run time."

    Reviewer round 3 nit: secret-named entries are tagged via
    `is_secret_env_name()` so the audit trail shows WHICH entries were
    sensitive without revealing the values. Format: `"NAME"` for benign
    entries, `"NAME:<secret-name>"` for secret-named entries. Values
    are NEVER recorded.
    """
    import os
    out: list[str] = []
    for name in sorted(os.environ.keys()):
        if is_secret_env_name(name):
            out.append(f"{name}:<secret-name>")
        else:
            out.append(name)
    return out
