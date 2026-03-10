import subprocess
import pytest
from pathlib import Path
from crucible.postmortem import PostmortemAnalyzer, PostmortemReport
from crucible.results import HEADER


def _make_results_tsv(path: Path, records: list[tuple[str, float, str, str]]) -> None:
    lines = [HEADER]
    for commit, metric, status, desc in records:
        lines.append(f"{commit}\t{metric}\t{status}\t{desc}")
    path.write_text("\n".join(lines) + "\n")


def _setup_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        'name: "test"\nfiles:\n  editable: ["train.py"]\ncommands:\n  run: "echo ok"\n  eval: "echo 0.5"\nmetric:\n  name: loss\n  direction: minimize\n'
    )
    (tmp_path / "train.py").write_text("x = 1")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)


def test_postmortem_summary_stats(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("aaa0001", 1.0, "keep", "baseline"),
        ("aaa0002", 1.1, "discard", "worse"),
        ("aaa0003", 0.8, "keep", "better"),
        ("aaa0004", 0.0, "crash", "oom"),
    ])
    analyzer = PostmortemAnalyzer(tmp_path, direction="maximize")
    report = analyzer.analyze()
    assert report.total == 4
    assert report.kept == 2
    assert report.discarded == 1
    assert report.crashed == 1
    assert report.best_metric == 1.0
    assert report.best_commit == "aaa0001"
    assert report.best_description == "baseline"


def test_postmortem_failure_streaks(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("aaa0001", 1.0, "keep", "baseline"),
        ("aaa0002", 1.1, "discard", "worse"),
        ("aaa0003", 0.0, "crash", "oom"),
        ("aaa0004", 1.2, "discard", "still worse"),
        ("aaa0005", 0.9, "keep", "recovered"),
    ])
    analyzer = PostmortemAnalyzer(tmp_path, direction="minimize")
    report = analyzer.analyze()
    assert len(report.failure_streaks) == 1
    assert report.failure_streaks[0]["start"] == 2
    assert report.failure_streaks[0]["length"] == 3


def test_postmortem_trend_data(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("aaa0001", 1.0, "keep", "baseline"),
        ("aaa0002", 0.8, "keep", "better"),
    ])
    analyzer = PostmortemAnalyzer(tmp_path, direction="minimize")
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
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("aaa0001", 1.0, "keep", "baseline"),
        ("aaa0002", 0.5, "keep", "better"),
        ("aaa0003", 0.8, "keep", "meh"),
    ])
    analyzer = PostmortemAnalyzer(tmp_path, direction="minimize")
    report = analyzer.analyze()
    assert report.best_metric == 0.5
    assert report.best_commit == "aaa0002"
    assert report.best_description == "better"
