import pytest
from pathlib import Path
from crucible.results import ResultsLog, ExperimentRecord


def test_init_creates_file_with_header(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    content = tsv.read_text()
    assert content.startswith("commit\tmetric_value\tstatus\tdescription\n")


def test_log_appends_record(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.log("a1b2c3d", 0.9979, "keep", "baseline")
    records = log.read_all()
    assert len(records) == 1
    assert records[0].commit == "a1b2c3d"
    assert records[0].metric_value == 0.9979
    assert records[0].status == "keep"


def test_read_last_n(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    for i in range(10):
        log.log(f"abc{i:04d}", float(i), "keep", f"exp {i}")
    last3 = log.read_last(3)
    assert len(last3) == 3
    assert last3[0].commit == "abc0007"


def test_best_record_minimize(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.log("aaa0001", 1.0, "keep", "first")
    log.log("aaa0002", 0.9, "keep", "second")
    log.log("aaa0003", 1.1, "discard", "third")
    best = log.best("minimize")
    assert best.commit == "aaa0002"


def test_best_record_maximize(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.log("aaa0001", 80.0, "keep", "first")
    log.log("aaa0002", 95.0, "keep", "second")
    best = log.best("maximize")
    assert best.commit == "aaa0002"


def test_is_improvement(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.log("aaa0001", 1.0, "keep", "baseline")
    assert log.is_improvement(0.99, "minimize") is True
    assert log.is_improvement(1.01, "minimize") is False
    assert log.is_improvement(1.0, "minimize") is False


def test_is_improvement_empty_log(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    assert log.is_improvement(1.0, "minimize") is True


def test_summary(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.log("aaa0001", 1.0, "keep", "baseline")
    log.log("aaa0002", 0.9, "keep", "better")
    log.log("aaa0003", 1.1, "discard", "worse")
    log.log("aaa0004", 0.0, "crash", "oom")
    s = log.summary()
    assert s["total"] == 4
    assert s["kept"] == 2
    assert s["discarded"] == 1
    assert s["crashed"] == 1


def test_read_all_missing_file(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    assert log.read_all() == []


def test_read_from_string(tmp_path):
    tsv_content = "commit\tmetric_value\tstatus\tdescription\nabc1234\t0.5\tkeep\tfirst\ndef5678\t0.3\tdiscard\tsecond\n"
    records = ResultsLog.read_from_string(tsv_content)
    assert len(records) == 2
    assert records[0].metric_value == 0.5
    assert records[1].status == "discard"


def test_description_with_tabs(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.log("aaa0001", 1.0, "keep", "desc\twith\ttabs")
    records = log.read_all()
    assert records[0].description == "desc\twith\ttabs"


def test_seed_baseline(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    records = log.read_all()
    assert len(records) == 1
    assert records[0].status == "baseline"
    assert records[0].metric_value == 600.0
    assert records[0].commit == "abc1234"
    assert "run1" in records[0].description


def test_best_includes_baseline_maximize(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    best = log.best("maximize")
    assert best is not None
    assert best.metric_value == 600.0
    assert best.status == "baseline"


def test_best_includes_baseline_minimize(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(0.3, "abc1234", "run1")
    best = log.best("minimize")
    assert best is not None
    assert best.metric_value == 0.3


def test_is_improvement_with_baseline_maximize(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    assert log.is_improvement(601.0, "maximize") is True
    assert log.is_improvement(600.0, "maximize") is False
    assert log.is_improvement(500.0, "maximize") is False


def test_is_improvement_with_baseline_minimize(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(0.5, "abc1234", "run1")
    assert log.is_improvement(0.4, "minimize") is True
    assert log.is_improvement(0.5, "minimize") is False
    assert log.is_improvement(0.6, "minimize") is False


def test_best_prefers_keep_over_baseline_when_better(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    log.log("def5678", 700.0, "keep", "improvement")
    best = log.best("maximize")
    assert best.metric_value == 700.0
    assert best.status == "keep"


def test_summary_excludes_baseline(tmp_path):
    """Baseline records should not be counted in summary totals."""
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    log.log("def5678", 700.0, "keep", "improvement")
    s = log.summary()
    assert s["total"] == 1  # baseline not counted
    assert s["kept"] == 1
