"""SubscriptionCLIAdapter base — M3 PR 16.

ABC for adapters that wrap subscription CLIs (Claude Code / Codex /
Gemini) as Crucible agent backends. Concrete implementations live in
sibling modules: `claude_code_cli.py` (active), `codex_cli.py` /
`gemini_cli.py` (stubs gated to PR 16b/c).

Reviewer round 1 framing:
- Spec §3.3: CLIs are complete agent products. We cannot extract raw
  completions; we drive the CLI in `--print` / `--no-conversation` mode
  and parse structured tool calls from stdout.
- Spec §3.2: each adapter is gated by a benign-parse compliance gate
  (≥99% for M3 release, ≥95% for POC admit).
- Spec §INV-3 belt-and-braces: the adapter MUST refuse CLI flags that
  re-enable CodeAct / shell / eval modes when the CLI supports it.
- Reviewer Q5: timeout → terminate → 5s grace → kill; stdout cap
  TERMINATES the subprocess (doesn't just truncate buffer).

Adapters subclass this ABC and implement:
  - `cli_name` (class attribute): adapter identifier
  - `default_binary_name` (class attribute): for PATH lookup
  - `build_argv(prompt, scratch_dir)`: per-CLI argv construction
  - `parse_output(stdout, exit_code)`: per-CLI output parsing →
    `(modified_files, description, structured_events, tool_was_called)`
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from crucible.agents.base import (
    AgentErrorType,
    AgentInterface,
    AgentResult,
)
from crucible.agents.cli_subscription.redaction import redact_argv

logger = logging.getLogger(__name__)


# Stable backend_kind written to AttemptNode (per spec §4.1 snake_case).
BACKEND_KIND = "cli_subscription"

# Isolation tag written to AttemptNode metadata. Parallel to spec
# §11.2 Q5 resolution `isolation=local_unsafe` — this one tags
# CLI-subscription runs as ACL-degraded. Reviewer round 1 Q8.
ISOLATION_TAG = "cli_subscription_unsandboxed"


class CLIBinaryError(RuntimeError):
    """Raised when the CLI binary cannot be located or doesn't run."""


class CLISubscriptionAuthError(RuntimeError):
    """Base class for adapter-raised auth-failure exceptions.

    `SubscriptionCLIBackend` isinstance-catches this base in its
    `generate_edit` pipeline and maps to `AgentErrorType.AUTH`.
    Concrete adapters (CodexCLIAdapter, GeminiCLIAdapter, future) raise
    a subclass with adapter-specific `evidence` and an instructional
    next-step in the message — never a phantom command (PR 16 R2 lesson).

    Why a base class: typed isinstance dispatch lets the backend route
    auth failures by exception TYPE rather than by string-matching the
    exception message (PR 19 round 2 lesson). Per PR 16a R2 follow-up
    #4, the abstraction was deferred until a second concrete adapter
    motivated it. PR 16b (this one, GeminiCLIAdapter) is that second
    instance.
    """

    def __init__(self, evidence: str) -> None:
        super().__init__(evidence)
        self.evidence = evidence


@dataclass
class AdapterRunContext:
    """Per-call context the adapter needs to construct argv + parse output."""
    prompt: str
    scratch_dir: Path
    workspace_root: Path
    timeout_seconds: int
    stdout_cap_bytes: int


@dataclass
class AdapterRawResult:
    """What `_run_subprocess()` returns to the adapter's parser."""
    argv_redacted: list[str]
    stdout: str
    stderr_tail: str
    exit_code: int
    timed_out: bool
    stdout_cap_exceeded: bool
    duration_seconds: float


@dataclass
class ParsedAdapterOutput:
    """What `parse_output()` produces; consumed by `generate_edit()`."""
    modified_files: list[Path]
    description: str
    # Structured events extracted from the CLI's stream output (e.g.
    # NDJSON lines from `--output-format=stream-json`). Used by the
    # safety-filter detector. Empty list if adapter doesn't emit them.
    structured_events: list[dict[str, Any]] = field(default_factory=list)
    # Whether any tool call was actually invoked during the run.
    # Used by the safety-filter tri-state detector — tool-was-called
    # without refusal phrases is strong evidence of NOT_DETECTED.
    tool_was_called: Optional[bool] = None
    # Optional schema_version of the structured event stream. If
    # adapter detects a schema it doesn't recognise, set this and
    # `_run_subprocess` -> `parse_output` chain emits parse_failure.
    unknown_schema: bool = False


class SubscriptionCLIAdapter(ABC):
    """Base for CLI subscription adapters.

    Subclass and set:
      - `cli_name` — matches `CLISubscriptionConfig.adapter`
      - `default_binary_name` — what to look for on PATH

    Implement:
      - `build_argv(ctx)` -> Sequence[str]
      - `parse_output(raw)` -> ParsedAdapterOutput

    The base class handles binary resolution, version snapshotting,
    subprocess management with timeout / stdout cap, redaction, and
    isolation tagging.
    """

    cli_name: str = ""
    default_binary_name: str = ""

    def __init__(
        self,
        *,
        cli_binary_path: Optional[str] = None,
    ) -> None:
        if not self.cli_name:
            raise NotImplementedError(
                f"{type(self).__name__} must set class attribute `cli_name`"
            )
        if not self.default_binary_name:
            raise NotImplementedError(
                f"{type(self).__name__} must set class attribute `default_binary_name`"
            )
        # Resolve binary AT CONSTRUCT (reviewer Q3): snapshot and reuse
        # for the lifetime of the adapter. If the binary is replaced
        # mid-run, the version delta is detectable from the ledger.
        self.cli_binary_path: Path = self._resolve_binary(cli_binary_path)
        self.cli_version: str = self._read_version()

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def build_argv(self, ctx: AdapterRunContext) -> Sequence[str]:
        """Return the argv to invoke the CLI for `ctx.prompt`.

        Must NOT include CLI flags that re-enable CodeAct / shell / eval
        modes (spec §INV-3 belt-and-braces). Should use `--print`-style
        non-interactive modes so output is parseable.
        """
        ...

    @abstractmethod
    def parse_output(self, raw: AdapterRawResult) -> ParsedAdapterOutput:
        """Parse the captured stdout into structured fields."""
        ...

    def version_command(self) -> Sequence[str]:
        """argv for `--version` lookup. Override if your CLI uses a different flag."""
        return [str(self.cli_binary_path), "--version"]

    # ------------------------------------------------------------------
    # Binary resolution + version snapshot (reviewer Q3)
    # ------------------------------------------------------------------

    def _resolve_binary(self, override: Optional[str]) -> Path:
        if override:
            p = Path(override).expanduser().resolve()
            if not p.exists():
                raise CLIBinaryError(
                    f"{self.cli_name}: cli_binary_path={p!s} does not exist"
                )
            if not os.access(p, os.X_OK):
                raise CLIBinaryError(
                    f"{self.cli_name}: cli_binary_path={p!s} is not executable"
                )
            return p
        found = shutil.which(self.default_binary_name)
        if not found:
            raise CLIBinaryError(
                f"{self.cli_name}: '{self.default_binary_name}' not found on PATH. "
                f"Install the CLI or set agent.cli_subscription.cli_binary_path."
            )
        return Path(found).resolve()

    def _read_version(self) -> str:
        """Snapshot CLI version by running `<binary> --version` once.

        Reviewer Q3: snapshot at construct time. If the binary is
        upgraded mid-run, the cached version detects it via ledger
        delta. Returns "unknown" on any failure (never raises) — best-
        effort metadata, not a hard contract.
        """
        try:
            result = subprocess.run(
                self.version_command(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            out = (result.stdout or result.stderr or "").strip().splitlines()
            return out[0] if out else "unknown"
        except (subprocess.TimeoutExpired, OSError):
            return "unknown"

    # ------------------------------------------------------------------
    # Subprocess management (reviewer Q5)
    # ------------------------------------------------------------------

    def run_subprocess(self, ctx: AdapterRunContext) -> AdapterRawResult:
        """Invoke the CLI with the configured timeout + stdout cap.

        Reviewer Q5:
          - On timeout: terminate → 5s grace → kill
          - On stdout-cap: kill subprocess (don't keep it running)
          - Capture partial stdout for forensics in either case
        """
        argv = list(self.build_argv(ctx))
        argv_redacted = redact_argv(argv)

        t0 = time.monotonic()
        timed_out = False
        stdout_cap_exceeded = False
        stdout_chunks: list[bytes] = []
        captured_size = 0
        stderr_text = ""
        exit_code = -1

        # We need streaming so we can enforce the stdout cap. Popen in
        # binary mode + manual loop with select/read.
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ctx.workspace_root),
            env=self._build_subprocess_env(),
        )

        deadline = t0 + float(ctx.timeout_seconds)
        try:
            while True:
                if proc.stdout is None:
                    break
                # Non-blocking-ish poll: read available bytes with a
                # short select, check deadlines between reads.
                remaining_time = max(0.0, deadline - time.monotonic())
                if remaining_time <= 0:
                    timed_out = True
                    break
                try:
                    chunk = proc.stdout.read1(64 * 1024)
                except (ValueError, OSError):
                    break
                if not chunk:
                    if proc.poll() is not None:
                        break
                    # No data but process still alive — short sleep.
                    time.sleep(0.05)
                    continue
                captured_size += len(chunk)
                if captured_size > ctx.stdout_cap_bytes:
                    stdout_cap_exceeded = True
                    # Truncate the last chunk to keep total ≤ cap, then
                    # break to terminate the process (don't leak more).
                    overflow = captured_size - ctx.stdout_cap_bytes
                    chunk = chunk[: max(0, len(chunk) - overflow)]
                    if chunk:
                        stdout_chunks.append(chunk)
                    break
                stdout_chunks.append(chunk)
        finally:
            if timed_out or stdout_cap_exceeded or proc.poll() is None:
                # Terminate then kill (reviewer Q5 refinement).
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                except (ProcessLookupError, OSError):
                    pass
            # Drain any remaining stderr (best-effort, post-termination)
            try:
                if proc.stderr is not None:
                    stderr_text = proc.stderr.read().decode(
                        "utf-8", errors="replace"
                    )
            except (ValueError, OSError):
                stderr_text = ""
            exit_code = proc.returncode if proc.returncode is not None else -1

        stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        # Cap stderr at 50 KB (we don't display giant stderr in HTML).
        stderr_tail = (stderr_text or "")[-50_000:]
        duration = time.monotonic() - t0

        return AdapterRawResult(
            argv_redacted=argv_redacted,
            stdout=stdout_text,
            stderr_tail=stderr_tail,
            exit_code=exit_code,
            timed_out=timed_out,
            stdout_cap_exceeded=stdout_cap_exceeded,
            duration_seconds=duration,
        )

    def _build_subprocess_env(self) -> dict[str, str]:
        """Construct the env passed to the subprocess.

        Default: pass the parent's env unchanged (CLI tools rely on
        provider auth via well-known env vars). Subclasses can override
        to filter / rewrite. Per spec §11.1 INV-2, when running inside
        Docker mode, the env_allowlist is enforced at the orchestrator
        layer — adapter-level filtering is best-effort.
        """
        return dict(os.environ)


# ---------------------------------------------------------------------------
# Stub-only error for adapters gated to follow-up PRs (reviewer Q1: B+stubs)
# ---------------------------------------------------------------------------


class AdapterNotImplementedError(NotImplementedError):
    """Raised by stub adapters whose implementation lands in a follow-up PR.

    The framework recognises the stub class so factory dispatch +
    `_SUPPORTED_CLI_ADAPTERS` validation work from day 1, but actual
    `generate_edit` calls fail with this clear error.
    """
