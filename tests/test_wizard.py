"""Tests for the experiment wizard."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from crucible.wizard import ExperimentWizard

MOCK_ANALYZE_RESPONSE = json.dumps({
    "inferred": {
        "name": "sorting-experiment",
        "metric_name": "throughput",
        "metric_direction": "maximize",
        "editable_files": ["solution.py"],
        "timeout_seconds": 60,
    },
    "uncertain": [
        {
            "param": "measurement_method",
            "question": "How should we measure speed?",
            "choices": [
                {"label": "Elements per second", "explanation": "Measures raw throughput"},
                {"label": "Time for 10K elements", "explanation": "Measures latency"},
            ],
        }
    ],
})

MOCK_GENERATE_RESPONSE = json.dumps({
    "files": {
        ".crucible/config.yaml": "name: sorting\nfiles:\n  editable: [solution.py]\ncommands:\n  run: \"echo ok\"\n  eval: \"echo throughput: 1\"\nmetric:\n  name: throughput\n  direction: maximize\n",
        ".crucible/program.md": "# Sort Optimization\nMaximize throughput.\n",
        "evaluate.py": "print('throughput: 100')\n",
        "solution.py": "def sort_fn(arr): return sorted(arr)\n",
    },
    "summary": "Metric: throughput (elements/sec, higher = better)",
})


def test_wizard_analyze_returns_questions():
    """analyze() should return inferred params and uncertain items."""
    wizard = ExperimentWizard()
    with patch("crucible.wizard._call_claude", return_value=MOCK_ANALYZE_RESPONSE):
        result = wizard.analyze("Optimize a sorting algorithm for throughput")
    assert result["inferred"]["metric_name"] == "throughput"
    assert len(result["uncertain"]) == 1


def test_wizard_generate_writes_files(tmp_path: Path):
    """generate() should write all files from the response."""
    wizard = ExperimentWizard()
    with patch("crucible.wizard._call_claude", return_value=MOCK_GENERATE_RESPONSE):
        summary = wizard.generate(
            description="Optimize sorting",
            decisions={"measurement_method": "Elements per second"},
            dest=tmp_path,
        )
    assert summary == "Metric: throughput (elements/sec, higher = better)"
    assert (tmp_path / ".crucible" / "config.yaml").exists()
    assert (tmp_path / ".crucible" / "program.md").exists()
    assert (tmp_path / "evaluate.py").exists()
    assert (tmp_path / "solution.py").exists()


def test_wizard_generate_creates_gitignore(tmp_path: Path):
    """generate() should create a .gitignore with results-*.tsv."""
    wizard = ExperimentWizard()
    with patch("crucible.wizard._call_claude", return_value=MOCK_GENERATE_RESPONSE):
        wizard.generate(
            description="Optimize sorting",
            decisions={},
            dest=tmp_path,
        )
    gitignore = (tmp_path / ".gitignore").read_text()
    assert "results-*.tsv" in gitignore


def test_wizard_analyze_includes_architecture_guards():
    """analyze() response with architecture_guards should be accepted."""
    response_with_guards = json.dumps({
        "inferred": {
            "name": "gomoku-alphazero",
            "metric_name": "win_rate",
            "metric_direction": "maximize",
            "editable_files": ["agent.py"],
            "timeout_seconds": 600,
            "architecture_guards": [
                "verify neural network forward() is called during choose_move",
                "cap agent.py LOC at 500",
                "ban more than 5 non-API function definitions",
            ],
        },
        "uncertain": [],
    })
    wizard = ExperimentWizard()
    with patch("crucible.wizard._call_claude", return_value=response_with_guards):
        result = wizard.analyze("AlphaZero Gomoku agent")
    assert "architecture_guards" in result["inferred"]
    assert len(result["inferred"]["architecture_guards"]) == 3


def test_analyze_prompt_mentions_architecture_guards():
    """The analyze system prompt should instruct Claude to infer architecture_guards."""
    from crucible.wizard import ANALYZE_SYSTEM_PROMPT
    assert "architecture_guards" in ANALYZE_SYSTEM_PROMPT


def test_generate_prompt_mentions_architecture_guards():
    """The generate system prompt should instruct Claude to embed guards in evaluate.py."""
    from crucible.wizard import GENERATE_SYSTEM_PROMPT
    assert "Architecture Guards" in GENERATE_SYSTEM_PROMPT
    assert "penalty" in GENERATE_SYSTEM_PROMPT.lower()


def test_detect_environment_returns_basic_info():
    """_detect_environment() should return OS, arch, and Python version."""
    from crucible.wizard import _detect_environment
    env = _detect_environment()
    assert "os" in env
    assert "arch" in env
    assert "python" in env


def test_format_environment_readable():
    """_format_environment() should produce a human-readable string."""
    from crucible.wizard import _format_environment
    env = {"os": "Darwin", "arch": "arm64", "python": "3.12.0", "apple_silicon": True}
    text = _format_environment(env)
    assert "Darwin" in text
    assert "Apple Silicon" in text


def test_analyze_appends_environment():
    """analyze() should append detected environment to the prompt sent to Claude."""
    wizard = ExperimentWizard()
    captured_prompts = []

    def mock_call(prompt, system_prompt=""):
        captured_prompts.append(prompt)
        return MOCK_ANALYZE_RESPONSE

    with patch("crucible.wizard._call_claude", side_effect=mock_call):
        wizard.analyze("Optimize sorting")

    assert len(captured_prompts) == 1
    assert "[Detected Runtime Environment]" in captured_prompts[0]
