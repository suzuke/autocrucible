import pytest
from crucible.guardrails import GuardRails, Violation


def test_no_violation_on_valid_edit():
    guard = GuardRails(editable=["train.py"], readonly=["prepare.py"])
    result = guard.check_edits(["train.py"])
    assert result is None


def test_readonly_violation():
    guard = GuardRails(editable=["train.py"], readonly=["prepare.py"])
    result = guard.check_edits(["train.py", "prepare.py"])
    assert result is not None
    assert result.kind == "readonly"
    assert "prepare.py" in result.message


def test_unlisted_file_violation():
    guard = GuardRails(editable=["train.py"], readonly=["prepare.py"])
    result = guard.check_edits(["train.py", "random.py"])
    assert result is not None
    assert result.kind == "unlisted"


def test_no_edits():
    guard = GuardRails(editable=["train.py"], readonly=["prepare.py"])
    result = guard.check_edits([])
    assert result is not None
    assert result.kind == "no_edits"


def test_check_metric_nan():
    guard = GuardRails(editable=["train.py"], readonly=[])
    assert guard.check_metric(float("nan")) is False
    assert guard.check_metric(float("inf")) is False
    assert guard.check_metric(0.997) is True
