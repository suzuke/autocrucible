import pytest
from pathlib import Path
from crucible.config import load_config, ConfigError

VALID_YAML = """\
name: "test-experiment"
description: "A test"

files:
  editable:
    - "train.py"
  readonly:
    - "prepare.py"

commands:
  run: "python train.py > run.log 2>&1"
  eval: "grep '^loss:' run.log"

metric:
  name: "loss"
  direction: "minimize"

constraints:
  timeout_seconds: 60
  max_retries: 3

agent:
  type: "claude-code"
  instructions: "program.md"
  context_window:
    include_history: true
    history_limit: 20
    include_best: true

git:
  branch_prefix: "crucible"
  tag_failed: true
"""

MINIMAL_YAML = """\
name: "minimal"
files:
  editable: ["train.py"]
commands:
  run: "python train.py"
  eval: "grep '^loss:' run.log"
metric:
  name: "loss"
  direction: "minimize"
"""


def test_load_valid_config(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(VALID_YAML)
    cfg = load_config(tmp_path)
    assert cfg.name == "test-experiment"
    assert cfg.files.editable == ["train.py"]
    assert cfg.files.readonly == ["prepare.py"]
    assert cfg.metric.direction == "minimize"
    assert cfg.constraints.timeout_seconds == 60
    assert cfg.agent.context_window.history_limit == 20
    assert cfg.git.tag_failed is True


def test_load_minimal_config_with_defaults(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(MINIMAL_YAML)
    cfg = load_config(tmp_path)
    assert cfg.name == "minimal"
    assert cfg.files.readonly == []
    assert cfg.constraints.timeout_seconds == 600
    assert cfg.constraints.max_retries == 3
    assert cfg.agent.type == "claude-code"
    assert cfg.git.branch_prefix == "crucible"


def test_load_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path)


def test_load_missing_required_fields(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("name: test\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)
