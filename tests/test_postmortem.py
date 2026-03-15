import subprocess
import unittest.mock
import pytest
from pathlib import Path
from crucible.postmortem import PostmortemAnalyzer, PostmortemReport, render_text
from crucible.results import ExperimentRecord, _serialize_record


def _make_results_jsonl(path: Path, records: list[tuple[str, float, str, str]]) -> None:
    lines = []
    for commit, metric, status, desc in records:
        lines.append(_serialize_record(ExperimentRecord(
            commit=commit, metric_value=metric, status=status, description=desc,
        )))
    path.write_text("\n".join(lines) + "\n")


def test_postmortem_summary_stats(tmp_path):
    results_path = tmp_path / "results-test.jsonl"
    _make_results_jsonl(results_path, [
        ("aaa0001", 1.0, "keep", "baseline"),
        ("aaa0002", 1.1, "discard", "worse"),
        ("aaa0003", 0.8, "keep", "better"),
        ("aaa0004", 0.0, "crash", "oom"),
    ])
    analyzer = PostmortemAnalyzer(results_path=results_path, direction="maximize")
    report = analyzer.analyze()
    assert report.total == 4
    assert report.kept == 2
    assert report.discarded == 1
    assert report.crashed == 1
    assert report.best_metric == 1.0
    assert report.best_commit == "aaa0001"
    assert report.best_description == "baseline"


def test_postmortem_failure_streaks(tmp_path):
    results_path = tmp_path / "results-test.jsonl"
    _make_results_jsonl(results_path, [
        ("aaa0001", 1.0, "keep", "baseline"),
        ("aaa0002", 1.1, "discard", "worse"),
        ("aaa0003", 0.0, "crash", "oom"),
        ("aaa0004", 1.2, "discard", "still worse"),
        ("aaa0005", 0.9, "keep", "recovered"),
    ])
    analyzer = PostmortemAnalyzer(results_path=results_path, direction="minimize")
    report = analyzer.analyze()
    assert len(report.failure_streaks) == 1
    assert report.failure_streaks[0]["start"] == 2
    assert report.failure_streaks[0]["length"] == 3


def test_postmortem_trend_data(tmp_path):
    results_path = tmp_path / "results-test.jsonl"
    _make_results_jsonl(results_path, [
        ("aaa0001", 1.0, "keep", "baseline"),
        ("aaa0002", 0.8, "keep", "better"),
    ])
    analyzer = PostmortemAnalyzer(results_path=results_path, direction="minimize")
    report = analyzer.analyze()
    assert len(report.trend) == 2
    assert report.trend[0] == {
        "iteration": 1,
        "metric": 1.0,
        "status": "keep",
        "description": "baseline",
        "commit": "aaa0001",
    }
    assert report.trend[1]["iteration"] == 2
    assert report.trend[1]["metric"] == 0.8


def test_postmortem_minimize_direction(tmp_path):
    results_path = tmp_path / "results-test.jsonl"
    _make_results_jsonl(results_path, [
        ("aaa0001", 1.0, "keep", "baseline"),
        ("aaa0002", 0.5, "keep", "better"),
        ("aaa0003", 0.8, "keep", "meh"),
    ])
    analyzer = PostmortemAnalyzer(results_path=results_path, direction="minimize")
    report = analyzer.analyze()
    assert report.best_metric == 0.5
    assert report.best_commit == "aaa0002"
    assert report.best_description == "better"


def test_render_text_contains_summary():
    report = PostmortemReport(
        total=3,
        kept=2,
        discarded=0,
        crashed=1,
        best_metric=20.0,
        best_commit="ccc3333",
        best_description="improved",
        trend=[
            {"iteration": 1, "metric": 10.0, "status": "keep", "description": "baseline", "commit": "aaa1111"},
            {"iteration": 2, "metric": 20.0, "status": "keep", "description": "improved", "commit": "ccc3333"},
            {"iteration": 3, "metric": 0.0, "status": "crash", "description": "broke it", "commit": "ddd4444"},
        ],
        failure_streaks=[],
    )
    text = render_text(report)
    assert "## Summary" in text
    assert "Best: 20.0" in text
    assert "ccc3333" in text
    assert "Kept: 2/3" in text
    assert "66%" in text
    assert "Crashed: 1" in text
    assert "\u2588" in text  # filled bar char
    assert "\u2591" in text  # empty bar char
    assert "\u2605" in text  # star marker on best
    assert "## Metric Trend" in text


def test_render_text_empty_results():
    report = PostmortemReport()
    text = render_text(report)
    assert text == "No iterations recorded."


def test_ai_insights_called_with_data(tmp_path):
    """Mock _call_claude_for_insights, verify report.ai_insights is set."""
    results_path = tmp_path / "results-test.jsonl"
    _make_results_jsonl(results_path, [
        ("aaa0001", 1.0, "keep", "baseline"),
        ("aaa0002", 0.8, "keep", "improved lr"),
        ("aaa0003", 0.0, "crash", "oom"),
    ])
    analyzer = PostmortemAnalyzer(results_path=results_path, direction="minimize")
    report = analyzer.analyze()

    with unittest.mock.patch(
        "crucible.postmortem._call_claude_for_insights",
        return_value="1. The learning rate change was the key turning point.",
    ) as mock_claude:
        analyzer.add_ai_insights(report)
        mock_claude.assert_called_once()
        assert report.ai_insights == "1. The learning rate change was the key turning point."


def test_ai_insights_prompt_contains_data(tmp_path):
    """Capture prompt passed to Claude, verify it contains metric values and descriptions."""
    results_path = tmp_path / "results-test.jsonl"
    _make_results_jsonl(results_path, [
        ("aaa0001", 1.0, "keep", "baseline"),
        ("aaa0002", 0.5, "keep", "better model"),
    ])
    analyzer = PostmortemAnalyzer(results_path=results_path, direction="minimize")
    report = analyzer.analyze()

    with unittest.mock.patch(
        "crucible.postmortem._call_claude_for_insights",
        return_value="insights here",
    ) as mock_claude:
        analyzer.add_ai_insights(report)
        prompt = mock_claude.call_args[0][0]
        assert "1.0" in prompt
        assert "0.5" in prompt
        assert "baseline" in prompt
        assert "better model" in prompt
        assert "minimize" in prompt
