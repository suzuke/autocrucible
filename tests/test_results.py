import json

import pytest
from pathlib import Path
from crucible.results import ResultsLog, ExperimentRecord, UsageInfo, results_filename


def _rec(commit="aaa0001", metric_value=1.0, status="keep", description="test", **kwargs):
    """Helper to create an ExperimentRecord with defaults."""
    return ExperimentRecord(commit=commit, metric_value=metric_value, status=status, description=description, **kwargs)


def test_init_creates_empty_file(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    assert f.read_text() == ""


def test_results_filename():
    assert results_filename("run1") == "results-run1.jsonl"


def test_log_appends_record(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.log(_rec(commit="a1b2c3d", metric_value=0.9979, status="keep", description="baseline"))
    records = log.read_all()
    assert len(records) == 1
    assert records[0].commit == "a1b2c3d"
    assert records[0].metric_value == 0.9979
    assert records[0].status == "keep"


def test_read_last_n(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    for i in range(10):
        log.log(_rec(commit=f"abc{i:04d}", metric_value=float(i), description=f"exp {i}"))
    last3 = log.read_last(3)
    assert len(last3) == 3
    assert last3[0].commit == "abc0007"


def test_best_record_minimize(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.log(_rec(commit="aaa0001", metric_value=1.0, description="first"))
    log.log(_rec(commit="aaa0002", metric_value=0.9, description="second"))
    log.log(_rec(commit="aaa0003", metric_value=1.1, status="discard", description="third"))
    best = log.best("minimize")
    assert best.commit == "aaa0002"


def test_best_record_maximize(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.log(_rec(commit="aaa0001", metric_value=80.0, description="first"))
    log.log(_rec(commit="aaa0002", metric_value=95.0, description="second"))
    best = log.best("maximize")
    assert best.commit == "aaa0002"


def test_is_improvement(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.log(_rec(commit="aaa0001", metric_value=1.0, description="baseline"))
    assert log.is_improvement(0.99, "minimize") is True
    assert log.is_improvement(1.01, "minimize") is False
    assert log.is_improvement(1.0, "minimize") is False


def test_is_improvement_empty_log(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    assert log.is_improvement(1.0, "minimize") is True


def test_summary(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.log(_rec(commit="aaa0001", metric_value=1.0, description="baseline"))
    log.log(_rec(commit="aaa0002", metric_value=0.9, description="better"))
    log.log(_rec(commit="aaa0003", metric_value=1.1, status="discard", description="worse"))
    log.log(_rec(commit="aaa0004", metric_value=0.0, status="crash", description="oom"))
    s = log.summary()
    assert s["total"] == 4
    assert s["kept"] == 2
    assert s["discarded"] == 1
    assert s["crashed"] == 1


def test_read_all_missing_file(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    assert log.read_all() == []


def test_read_from_string_jsonl():
    lines = [
        json.dumps({"commit": "abc1234", "metric_value": 0.5, "status": "keep", "description": "first"}),
        json.dumps({"commit": "def5678", "metric_value": 0.3, "status": "discard", "description": "second"}),
    ]
    content = "\n".join(lines) + "\n"
    records = ResultsLog.read_from_string(content)
    assert len(records) == 2
    assert records[0].metric_value == 0.5
    assert records[1].status == "discard"


def test_read_from_string_tsv_fallback():
    """Old TSV format is auto-detected and parsed correctly."""
    tsv_content = "commit\tmetric_value\tstatus\tdescription\nabc1234\t0.5\tkeep\tfirst\ndef5678\t0.3\tdiscard\tsecond\n"
    records = ResultsLog.read_from_string(tsv_content)
    assert len(records) == 2
    assert records[0].metric_value == 0.5
    assert records[1].status == "discard"


def test_seed_baseline(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    records = log.read_all()
    assert len(records) == 1
    assert records[0].status == "baseline"
    assert records[0].metric_value == 600.0
    assert records[0].commit == "abc1234"
    assert "run1" in records[0].description


def test_best_includes_baseline_maximize(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    best = log.best("maximize")
    assert best is not None
    assert best.metric_value == 600.0
    assert best.status == "baseline"


def test_best_includes_baseline_minimize(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.seed_baseline(0.3, "abc1234", "run1")
    best = log.best("minimize")
    assert best is not None
    assert best.metric_value == 0.3


def test_is_improvement_with_baseline_maximize(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    assert log.is_improvement(601.0, "maximize") is True
    assert log.is_improvement(600.0, "maximize") is False
    assert log.is_improvement(500.0, "maximize") is False


def test_is_improvement_with_baseline_minimize(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.seed_baseline(0.5, "abc1234", "run1")
    assert log.is_improvement(0.4, "minimize") is True
    assert log.is_improvement(0.5, "minimize") is False
    assert log.is_improvement(0.6, "minimize") is False


def test_best_prefers_keep_over_baseline_when_better(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    log.log(_rec(commit="def5678", metric_value=700.0, description="improvement"))
    best = log.best("maximize")
    assert best.metric_value == 700.0
    assert best.status == "keep"


def test_summary_excludes_baseline(tmp_path):
    """Baseline records should not be counted in summary totals."""
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    log.log(_rec(commit="def5678", metric_value=700.0, description="improvement"))
    s = log.summary()
    assert s["total"] == 1  # baseline not counted
    assert s["kept"] == 1


# --- New tests for JSONL features ---


def test_usage_info_defaults():
    u = UsageInfo()
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert u.estimated_cost_usd is None


def test_record_new_fields_default_none():
    r = ExperimentRecord(commit="abc", metric_value=1.0, status="keep", description="test")
    assert r.iteration is None
    assert r.timestamp is None
    assert r.delta is None
    assert r.delta_percent is None
    assert r.files_changed is None
    assert r.diff_stats is None
    assert r.duration_seconds is None
    assert r.usage is None


def test_jsonl_roundtrip_with_all_fields(tmp_path):
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    record = ExperimentRecord(
        commit="abc1234",
        metric_value=0.95,
        status="keep",
        description="full record",
        iteration=3,
        timestamp="2026-01-01T00:00:00",
        delta=0.05,
        delta_percent=5.0,
        files_changed=["model.py", "train.py"],
        diff_stats={"insertions": 10, "deletions": 5},
        duration_seconds=42.5,
        usage=UsageInfo(input_tokens=1000, output_tokens=500, estimated_cost_usd=0.01),
    )
    log.log(record)
    records = log.read_all()
    assert len(records) == 1
    r = records[0]
    assert r.commit == "abc1234"
    assert r.iteration == 3
    assert r.timestamp == "2026-01-01T00:00:00"
    assert r.delta == 0.05
    assert r.delta_percent == 5.0
    assert r.files_changed == ["model.py", "train.py"]
    assert r.diff_stats == {"insertions": 10, "deletions": 5}
    assert r.duration_seconds == 42.5
    assert r.usage.input_tokens == 1000
    assert r.usage.output_tokens == 500
    assert r.usage.estimated_cost_usd == 0.01


def test_forward_compat_missing_fields():
    """Records with only basic fields should deserialize with None for new fields."""
    line = json.dumps({"commit": "abc", "metric_value": 1.0, "status": "keep", "description": "old"})
    records = ResultsLog.read_from_string(line + "\n")
    assert len(records) == 1
    assert records[0].iteration is None
    assert records[0].usage is None


def test_none_values_not_serialized(tmp_path):
    """None values should be omitted from JSONL output."""
    f = tmp_path / "results.jsonl"
    log = ResultsLog(f)
    log.init()
    log.log(_rec())
    line = f.read_text().strip()
    d = json.loads(line)
    assert "iteration" not in d
    assert "usage" not in d
    assert "timestamp" not in d


def test_read_from_string_empty():
    assert ResultsLog.read_from_string("") == []
    assert ResultsLog.read_from_string("\n\n") == []
