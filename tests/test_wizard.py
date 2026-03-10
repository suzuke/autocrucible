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
    """generate() should create a .gitignore with results.tsv."""
    wizard = ExperimentWizard()
    with patch("crucible.wizard._call_claude", return_value=MOCK_GENERATE_RESPONSE):
        wizard.generate(
            description="Optimize sorting",
            decisions={},
            dest=tmp_path,
        )
    gitignore = (tmp_path / ".gitignore").read_text()
    assert "results.tsv" in gitignore
