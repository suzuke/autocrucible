"""SubscriptionCLI adapters for M3 PR 16.

Public exports:
  - `SubscriptionCLIAdapter` — ABC for CLI subprocess wrappers
  - `ClaudeCodeCLIAdapter` — wraps the `claude` CLI (active in PR 16)
  - `CodexCLIAdapter` — STUB, gated to PR 16b
  - `GeminiCLIAdapter` — STUB, gated to PR 16c
  - Compliance harness types from `compliance.py`
  - Tri-state safety detection from `safety.py`
"""

from __future__ import annotations

from crucible.agents.cli_subscription.base import (
    BACKEND_KIND,
    ISOLATION_TAG,
    AdapterNotImplementedError,
    AdapterRawResult,
    AdapterRunContext,
    CLIBinaryError,
    ParsedAdapterOutput,
    SubscriptionCLIAdapter,
)
from crucible.agents.cli_subscription.claude_code_cli import ClaudeCodeCLIAdapter
from crucible.agents.cli_subscription.codex_cli import CodexCLIAdapter
from crucible.agents.cli_subscription.compliance import (
    ADMIT_THRESHOLD,
    COMPLIANCE_FRESHNESS,
    RELEASE_THRESHOLD,
    BenignTask,
    ComplianceReport,
    TrialClassification,
    TrialResult,
    load_reports,
    persist_report,
    reports_dir_for,
    verify_recent_pass,
)
from crucible.agents.cli_subscription.gemini_cli import GeminiCLIAdapter
from crucible.agents.cli_subscription.safety import (
    SafetyDetection,
    SafetyFilterState,
    detect_safety_filter,
)

__all__ = [
    "BACKEND_KIND",
    "ISOLATION_TAG",
    "AdapterNotImplementedError",
    "AdapterRawResult",
    "AdapterRunContext",
    "CLIBinaryError",
    "ParsedAdapterOutput",
    "SubscriptionCLIAdapter",
    "ClaudeCodeCLIAdapter",
    "CodexCLIAdapter",
    "GeminiCLIAdapter",
    # compliance
    "ADMIT_THRESHOLD",
    "COMPLIANCE_FRESHNESS",
    "RELEASE_THRESHOLD",
    "BenignTask",
    "ComplianceReport",
    "TrialClassification",
    "TrialResult",
    "load_reports",
    "persist_report",
    "reports_dir_for",
    "verify_recent_pass",
    # safety
    "SafetyDetection",
    "SafetyFilterState",
    "detect_safety_filter",
]
