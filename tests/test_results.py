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


def test_description_with_tabs(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.log("aaa0001", 1.0, "keep", "desc\twith\ttabs")
    records = log.read_all()
    assert records[0].description == "desc\twith\ttabs"
