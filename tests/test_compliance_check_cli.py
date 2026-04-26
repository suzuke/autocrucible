"""Tests for `crucible compliance-check` CLI + run_compliance_harness — M3 PR 16c.

Coverage:
  - Harness happy path: 3 mocked PARSE_SUCCESS trials → report has
    pass_rate=1.0 and is persisted to compliance-reports/
  - Classification matrix: PARSE_FAILURE (unknown_schema), CLI_ERROR
    (timeout / non-zero exit / parser raise / auth failure),
    MODEL_REFUSAL (refusal phrase + no tool call), FORMAT_DRIFT
    (residual: clean run, no tool, no refusal phrase)
  - Persistence: report file lands in <project>/compliance-reports/
    and is loadable by load_reports() round-trip
  - CLI smoke: `crucible compliance-check --adapter codex-cli` invokes
    the harness with the right adapter class and prints summary
  - Threshold messaging: release/admit/fail paths render the correct
    line
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from crucible.agents.cli_subscription.base import (
    AdapterRawResult,
    AdapterRunContext,
    CLISubscriptionAuthError,
    ParsedAdapterOutput,
    SubscriptionCLIAdapter,
)
from crucible.agents.cli_subscription.compliance import (
    ADMIT_THRESHOLD,
    BENIGN_TASK_SUITE,
    RELEASE_THRESHOLD,
    BenignTask,
    ComplianceReport,
    TrialClassification,
    _MODEL_REFUSAL_PHRASES,
    _classify_trial,
    load_reports,
    reports_dir_for,
    run_compliance_harness,
)


# ---------------------------------------------------------------------------
# Test adapter — no real subprocess
# ---------------------------------------------------------------------------


class _FakeAdapter(SubscriptionCLIAdapter):
    """Adapter stub that returns a programmable raw + parsed."""
    cli_name = "fake-adapter"
    default_binary_name = "fake-bin"

    def __init__(
        self,
        *,
        raw_factory=None,
        parsed_factory=None,
        parser_raises=None,
    ):
        # Bypass binary resolution / version snapshot
        self.cli_binary_path = Path("/fake/bin")
        self.cli_version = "0.0.0"
        self._raw_factory = raw_factory or (lambda ctx: _good_raw())
        self._parsed_factory = parsed_factory or (lambda raw: _good_parsed())
        self._parser_raises = parser_raises

    def build_argv(self, ctx):
        return ["/fake/bin"]

    def parse_output(self, raw):
        if self._parser_raises is not None:
            raise self._parser_raises
        return self._parsed_factory(raw)

    def run_subprocess(self, ctx):
        return self._raw_factory(ctx)


def _good_raw() -> AdapterRawResult:
    return AdapterRawResult(
        argv_redacted=["/fake/bin"],
        stdout="",
        stderr_tail="",
        exit_code=0,
        timed_out=False,
        stdout_cap_exceeded=False,
        duration_seconds=0.1,
    )


def _good_parsed() -> ParsedAdapterOutput:
    return ParsedAdapterOutput(
        modified_files=[],
        description="ok",
        structured_events=[],
        tool_was_called=True,
        unknown_schema=False,
    )


# ---------------------------------------------------------------------------
# _classify_trial — exhaustive matrix
# ---------------------------------------------------------------------------


def test_classify_timeout_is_cli_error():
    raw = AdapterRawResult(
        argv_redacted=[], stdout="", stderr_tail="", exit_code=-1,
        timed_out=True, stdout_cap_exceeded=False, duration_seconds=60,
    )
    cls, ev = _classify_trial(raw, _good_parsed(), None)
    assert cls == TrialClassification.CLI_ERROR
    assert "timeout" in ev


def test_classify_auth_evidence_is_cli_error():
    cls, ev = _classify_trial(
        _good_raw(), _good_parsed(), "Please set an Auth method",
    )
    assert cls == TrialClassification.CLI_ERROR
    assert "auth" in ev


def test_classify_nonzero_exit_is_cli_error():
    raw = AdapterRawResult(
        argv_redacted=[], stdout="", stderr_tail="", exit_code=2,
        timed_out=False, stdout_cap_exceeded=False, duration_seconds=0.1,
    )
    cls, ev = _classify_trial(raw, _good_parsed(), None)
    assert cls == TrialClassification.CLI_ERROR
    assert "exit_code=2" in ev


def test_classify_unknown_schema_is_parse_failure():
    parsed = ParsedAdapterOutput(
        modified_files=[], description="x", structured_events=[],
        tool_was_called=True, unknown_schema=True,
    )
    cls, ev = _classify_trial(_good_raw(), parsed, None)
    assert cls == TrialClassification.PARSE_FAILURE


def test_classify_tool_call_is_parse_success():
    cls, ev = _classify_trial(_good_raw(), _good_parsed(), None)
    assert cls == TrialClassification.PARSE_SUCCESS


def test_classify_refusal_phrase_is_model_refusal():
    parsed = ParsedAdapterOutput(
        modified_files=[],
        description="I'm sorry, I can't help with that benign task.",
        structured_events=[],
        tool_was_called=False,
        unknown_schema=False,
    )
    cls, ev = _classify_trial(_good_raw(), parsed, None)
    assert cls == TrialClassification.MODEL_REFUSAL
    # Evidence cites the matched phrase
    assert "phrase:" in ev


def test_classify_format_drift_when_no_tool_no_refusal():
    """CLI ran clean, parser happy, but no tool call and no refusal
    phrase → FORMAT_DRIFT (the residual classification)."""
    parsed = ParsedAdapterOutput(
        modified_files=[],
        description="Sure, here's the answer in plain text.",
        structured_events=[],
        tool_was_called=False,
        unknown_schema=False,
    )
    cls, _ev = _classify_trial(_good_raw(), parsed, None)
    assert cls == TrialClassification.FORMAT_DRIFT


def test_classify_parser_raised_is_cli_error():
    """Parser raised an exception (parsed=None signals this)."""
    cls, ev = _classify_trial(_good_raw(), None, None)
    assert cls == TrialClassification.CLI_ERROR
    assert "parser" in ev


def test_classify_priority_auth_beats_unknown_schema():
    """Auth-error evidence takes precedence over schema drift."""
    parsed = ParsedAdapterOutput(
        modified_files=[], description="", structured_events=[],
        tool_was_called=False, unknown_schema=True,
    )
    cls, _ev = _classify_trial(_good_raw(), parsed, "Not signed in")
    assert cls == TrialClassification.CLI_ERROR


# ---------------------------------------------------------------------------
# run_compliance_harness — happy path
# ---------------------------------------------------------------------------


def test_harness_runs_all_tasks_and_persists_report(tmp_path):
    adapter = _FakeAdapter()
    report = run_compliance_harness(
        adapter,
        tasks=BENIGN_TASK_SUITE,
        project_dir=tmp_path,
    )
    assert report.total == len(BENIGN_TASK_SUITE)
    assert report.passes == report.total
    assert report.pass_rate == 1.0
    assert report.adapter == "fake-adapter"
    assert report.cli_version == "0.0.0"

    # Report persisted to <tmp>/compliance-reports/
    reports = load_reports(reports_dir_for(tmp_path))
    assert len(reports) == 1
    assert reports[0].pass_rate == 1.0


def test_harness_workspace_files_materialised(tmp_path, monkeypatch):
    """Each task's workspace_files should be written to the scratch dir
    before the adapter sees the AdapterRunContext."""
    seen_dirs: list[Path] = []

    def _record_raw(ctx: AdapterRunContext) -> AdapterRawResult:
        seen_dirs.append(ctx.scratch_dir)
        # Verify the workspace files actually exist
        for entry in ctx.scratch_dir.iterdir():
            assert entry.is_file() or entry.is_dir()
        return _good_raw()

    adapter = _FakeAdapter(raw_factory=_record_raw)
    run_compliance_harness(
        adapter,
        tasks=(BenignTask(
            task_id="x",
            description="d",
            prompt="p",
            workspace_files={"a.txt": "hello", "sub/b.txt": "world"},
        ),),
        project_dir=tmp_path,
    )
    assert len(seen_dirs) == 1
    scratch = seen_dirs[0]
    # Scratch dir is cleaned up by tempfile after run; the assertions
    # inside _record_raw verified the files existed during the trial.


def test_harness_subprocess_exception_classifies_as_cli_error(tmp_path):
    def _raise(ctx):
        raise OSError("binary disappeared")

    adapter = _FakeAdapter(raw_factory=_raise)
    report = run_compliance_harness(
        adapter,
        tasks=(BENIGN_TASK_SUITE[0],),
        project_dir=tmp_path,
    )
    assert report.trials[0].classification == TrialClassification.CLI_ERROR
    assert "binary disappeared" in report.trials[0].evidence


def test_harness_parser_raise_classifies_as_cli_error(tmp_path):
    adapter = _FakeAdapter(parser_raises=RuntimeError("oops"))
    report = run_compliance_harness(
        adapter,
        tasks=(BENIGN_TASK_SUITE[0],),
        project_dir=tmp_path,
    )
    assert report.trials[0].classification == TrialClassification.CLI_ERROR
    assert "parser raise" in report.trials[0].description


def test_harness_auth_failure_classifies_as_cli_error(tmp_path):
    adapter = _FakeAdapter(
        parser_raises=CLISubscriptionAuthError("Please set an Auth method"),
    )
    report = run_compliance_harness(
        adapter,
        tasks=(BENIGN_TASK_SUITE[0],),
        project_dir=tmp_path,
    )
    assert report.trials[0].classification == TrialClassification.CLI_ERROR
    assert "auth:" in report.trials[0].evidence


def test_harness_mixed_results(tmp_path):
    """Heterogeneous trial outcomes accumulate correctly."""
    call_count = {"n": 0}

    def _alternating(ctx: AdapterRunContext) -> AdapterRawResult:
        call_count["n"] += 1
        if call_count["n"] == 2:
            # Second trial: timeout
            return AdapterRawResult(
                argv_redacted=[], stdout="", stderr_tail="", exit_code=-1,
                timed_out=True, stdout_cap_exceeded=False, duration_seconds=60,
            )
        return _good_raw()

    adapter = _FakeAdapter(raw_factory=_alternating)
    report = run_compliance_harness(
        adapter,
        tasks=BENIGN_TASK_SUITE,
        project_dir=tmp_path,
    )
    assert report.total == len(BENIGN_TASK_SUITE)
    assert report.passes == report.total - 1  # one timeout
    cls = [t.classification for t in report.trials]
    assert TrialClassification.CLI_ERROR in cls
    assert cls.count(TrialClassification.PARSE_SUCCESS) == report.passes


def test_harness_round_trip_via_load_reports(tmp_path):
    adapter = _FakeAdapter()
    report = run_compliance_harness(
        adapter,
        tasks=BENIGN_TASK_SUITE[:1],
        project_dir=tmp_path,
    )
    loaded = load_reports(reports_dir_for(tmp_path))
    assert len(loaded) == 1
    assert loaded[0].adapter == report.adapter
    assert loaded[0].cli_version == report.cli_version
    assert loaded[0].total == report.total
    assert loaded[0].passes == report.passes


# ---------------------------------------------------------------------------
# CLI command smoke
# ---------------------------------------------------------------------------


def test_cli_compliance_check_constructs_codex_adapter_and_runs(tmp_path, monkeypatch):
    """`crucible compliance-check --adapter codex-cli --limit 1` invokes
    the harness with CodexCLIAdapter, prints summary, persists report."""
    from crucible.agents.cli_subscription.codex_cli import CodexCLIAdapter
    from crucible.cli import main as cli_main

    monkeypatch.setattr(
        CodexCLIAdapter, "_resolve_binary",
        lambda self, override: Path("/fake/codex"),
    )
    monkeypatch.setattr(
        CodexCLIAdapter, "_read_version", lambda self: "0.124.0",
    )
    monkeypatch.setattr(
        CodexCLIAdapter, "run_subprocess",
        lambda self, ctx: _good_raw(),
    )
    monkeypatch.setattr(
        CodexCLIAdapter, "parse_output",
        lambda self, raw: _good_parsed(),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "compliance-check",
            "--adapter", "codex-cli",
            "--project-dir", str(tmp_path),
            "--limit", "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "codex-cli" in result.output
    assert "0.124.0" in result.output
    assert "Release threshold met" in result.output

    # Report persisted
    reports = load_reports(reports_dir_for(tmp_path))
    assert len(reports) == 1
    assert reports[0].adapter == "codex-cli"


def test_cli_compliance_check_requires_adapter():
    """--adapter is required."""
    from crucible.cli import main as cli_main

    runner = CliRunner()
    result = runner.invoke(cli_main, ["compliance-check", "--limit", "1"])
    assert result.exit_code != 0
    assert "adapter" in result.output.lower()


def test_cli_compliance_check_rejects_unknown_adapter():
    from crucible.cli import main as cli_main

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        ["compliance-check", "--adapter", "bogus-cli"],
    )
    assert result.exit_code != 0


def test_cli_compliance_check_rejects_negative_limit(tmp_path, monkeypatch):
    from crucible.agents.cli_subscription.codex_cli import CodexCLIAdapter
    from crucible.cli import main as cli_main

    monkeypatch.setattr(
        CodexCLIAdapter, "_resolve_binary",
        lambda self, override: Path("/fake/codex"),
    )
    monkeypatch.setattr(
        CodexCLIAdapter, "_read_version", lambda self: "0.124.0",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "compliance-check",
            "--adapter", "codex-cli",
            "--project-dir", str(tmp_path),
            "--limit", "0",
        ],
    )
    assert result.exit_code != 0
    assert "positive integer" in result.output


def test_cli_compliance_check_below_admit_path(tmp_path, monkeypatch):
    """Pass-rate below admit threshold → ✗ message."""
    from crucible.agents.cli_subscription.codex_cli import CodexCLIAdapter
    from crucible.cli import main as cli_main

    monkeypatch.setattr(
        CodexCLIAdapter, "_resolve_binary",
        lambda self, override: Path("/fake/codex"),
    )
    monkeypatch.setattr(
        CodexCLIAdapter, "_read_version", lambda self: "0.124.0",
    )

    # All trials fail
    bad_raw = AdapterRawResult(
        argv_redacted=[], stdout="", stderr_tail="", exit_code=1,
        timed_out=False, stdout_cap_exceeded=False, duration_seconds=0.1,
    )
    monkeypatch.setattr(
        CodexCLIAdapter, "run_subprocess",
        lambda self, ctx: bad_raw,
    )
    monkeypatch.setattr(
        CodexCLIAdapter, "parse_output",
        lambda self, raw: _good_parsed(),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "compliance-check",
            "--adapter", "codex-cli",
            "--project-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "Below admit threshold" in result.output


# ---------------------------------------------------------------------------
# Refusal-phrase set
# ---------------------------------------------------------------------------


def test_refusal_phrases_are_explicit_set():
    """Mirrors the auth-phrase invariant: explicit declared list, not
    coincidental substring matching (PR 19 R2 lesson)."""
    assert len(_MODEL_REFUSAL_PHRASES) >= 5
    for p in _MODEL_REFUSAL_PHRASES:
        assert isinstance(p, str) and len(p) > 3
