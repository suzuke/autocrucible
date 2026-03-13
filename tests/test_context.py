import pytest
from pathlib import Path
from crucible.context import ContextAssembler, _strategy_hint, _classify_crash
from crucible.config import (
    Config, FilesConfig, CommandsConfig, MetricConfig,
    ConstraintsConfig, AgentConfig, ContextWindowConfig, GitConfig,
)
from crucible.results import ExperimentRecord, ResultsLog


def make_config(tmp_path, include_history=True, history_limit=20):
    return Config(
        name="test",
        files=FilesConfig(editable=["train.py"], readonly=["prepare.py"]),
        commands=CommandsConfig(run="python train.py", eval="grep loss run.log"),
        metric=MetricConfig(name="loss", direction="minimize"),
        constraints=ConstraintsConfig(),
        agent=AgentConfig(
            instructions="program.md",
            context_window=ContextWindowConfig(
                include_history=include_history,
                history_limit=history_limit,
                include_best=True,
            ),
        ),
        git=GitConfig(),
    )


def test_assemble_with_no_history(tmp_path):
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("You are a researcher.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/test")
    prompt = ctx.assemble(log)
    assert "You are a researcher." in prompt
    assert "crucible/test" in prompt
    assert "Editable files: train.py" in prompt
    assert "Your Task" in prompt


def test_assemble_with_history(tmp_path):
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("Instructions here.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.log("aaa0001", 1.0, "keep", "baseline")
    log.log("aaa0002", 0.95, "keep", "better LR")
    log.log("aaa0003", 1.1, "discard", "worse activation")
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/test")
    prompt = ctx.assemble(log)
    assert "baseline" in prompt
    assert "better LR" in prompt
    assert "Best loss so far: 0.95" in prompt


def test_assemble_respects_history_limit(tmp_path):
    cfg = make_config(tmp_path, history_limit=2)
    (tmp_path / "program.md").write_text("Instructions.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    for i in range(10):
        log.log(f"aaa{i:04d}", float(i), "keep", f"exp {i}")
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/test")
    prompt = ctx.assemble(log)
    assert "exp 9" in prompt
    assert "exp 8" in prompt
    assert "exp 0" not in prompt


def test_assemble_with_error_context(tmp_path):
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("Instructions.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/test")
    ctx.add_error("Readonly file modified: prepare.py")
    prompt = ctx.assemble(log)
    assert "prepare.py" in prompt
    assert "Error" in prompt or "error" in prompt


def test_assemble_with_crash_info(tmp_path):
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("Instructions.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/test")
    ctx.add_crash_info("RuntimeError: CUDA out of memory")
    prompt = ctx.assemble(log)
    assert "CUDA out of memory" in prompt


# -- Strategy tier tests -------------------------------------------------------

def _make_record(status):
    return ExperimentRecord(
        commit="abc1234", metric_value=1.0, status=status, description="test"
    )


def test_strategy_hint_no_records():
    hint = _strategy_hint([])
    assert "Tier 1" in hint
    assert "EXPLORE" in hint


def test_strategy_hint_last_kept():
    records = [_make_record("keep")]
    hint = _strategy_hint(records)
    assert "Tier 1" in hint
    assert "EXPLOIT" in hint


def test_strategy_hint_tier2():
    records = [_make_record("keep"), _make_record("discard"), _make_record("crash")]
    hint = _strategy_hint(records)
    assert "Tier 2" in hint
    assert "RE-READ" in hint


def test_strategy_hint_tier3():
    records = [_make_record("keep")] + [_make_record("discard")] * 4
    hint = _strategy_hint(records)
    assert "Tier 3" in hint
    assert "COMBINE" in hint


def test_strategy_hint_tier4():
    records = [_make_record("keep")] + [_make_record("discard")] * 7
    hint = _strategy_hint(records)
    assert "Tier 4" in hint
    assert "RADICAL" in hint


# -- Crash classification tests -----------------------------------------------

def test_classify_crash_syntax():
    diag, advice = _classify_crash("  File 'x.py', line 5\nSyntaxError: invalid syntax")
    assert diag == "Typo"
    assert "ABANDON" not in advice


def test_classify_crash_import():
    diag, advice = _classify_crash("ModuleNotFoundError: No module named 'foo'")
    assert diag == "Missing module"
    assert "ABANDON" in advice


def test_classify_crash_oom():
    diag, advice = _classify_crash("torch.cuda.OutOfMemoryError: CUDA out of memory")
    assert diag == "Resource limit"
    assert "ABANDON" in advice


def test_classify_crash_timeout():
    diag, advice = _classify_crash("TIMED OUT after 300s")
    assert diag == "Too slow"
    assert "ABANDON" in advice


def test_classify_crash_unknown():
    diag, _ = _classify_crash("some weird error nobody expected")
    assert diag == "Unknown"


def test_crash_classification_in_assembled_output(tmp_path):
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("Instructions.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/test")
    ctx.add_crash_info("NameError: name 'xyz' is not defined")
    prompt = ctx.assemble(log)
    assert "Diagnosis: Typo" in prompt
    assert "NameError" in prompt


# -- Crash info requeue tests ------------------------------------------------

def test_crash_info_cleared_after_assemble(tmp_path):
    """Crash info is cleared after assemble (existing behavior)."""
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("Instructions.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/test")
    ctx.add_crash_info("SyntaxError: invalid syntax")
    prompt1 = ctx.assemble(log)
    assert "SyntaxError" in prompt1
    prompt2 = ctx.assemble(log)
    assert "SyntaxError" not in prompt2


def test_crash_info_requeued_survives_assemble(tmp_path):
    """Requeued crash info appears in the next assemble."""
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("Instructions.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/test")
    ctx.add_crash_info("SyntaxError: invalid syntax")
    ctx.assemble(log)  # clears crash info, saves to _last_crash_info
    ctx.requeue_crash_info()  # re-queue from last
    prompt = ctx.assemble(log)
    assert "SyntaxError" in prompt


def test_crash_info_not_requeued_without_call(tmp_path):
    """Without requeue_crash_info(), crash info stays cleared."""
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("Instructions.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/test")
    ctx.add_crash_info("SyntaxError: invalid syntax")
    ctx.assemble(log)
    # No requeue call
    prompt = ctx.assemble(log)
    assert "SyntaxError" not in prompt


# -- Baseline-aware context tests --------------------------------------------

def test_assemble_shows_baseline_info(tmp_path):
    """Baseline record should show in state section with special label."""
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("Instructions.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/run2")
    prompt = ctx.assemble(log)
    assert "600.0" in prompt
    assert "baseline" in prompt.lower() or "Baseline" in prompt


def test_assemble_baseline_only_shows_explore_strategy(tmp_path):
    """With only a baseline record (no real experiments), strategy should be EXPLORE."""
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("Instructions.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/run2")
    prompt = ctx.assemble(log)
    assert "EXPLORE" in prompt


def test_assemble_baseline_not_in_history_table(tmp_path):
    """Baseline record should not appear as a row in the experiment history table."""
    cfg = make_config(tmp_path)
    (tmp_path / "program.md").write_text("Instructions.")
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    log.log("def5678", 650.0, "keep", "first real improvement")
    ctx = ContextAssembler(cfg, tmp_path, branch_name="crucible/run2")
    prompt = ctx.assemble(log)
    # "Forked from" is the baseline description — should NOT be in history table
    assert "Forked from" not in prompt
    # But the real experiment should be there
    assert "first real improvement" in prompt
