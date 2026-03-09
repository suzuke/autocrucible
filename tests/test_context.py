import pytest
from pathlib import Path
from crucible.context import ContextAssembler
from crucible.config import (
    Config, FilesConfig, CommandsConfig, MetricConfig,
    ConstraintsConfig, AgentConfig, ContextWindowConfig, GitConfig,
)
from crucible.results import ResultsLog


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
