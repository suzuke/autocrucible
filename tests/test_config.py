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


def test_system_prompt_config(tmp_path):
    config_yaml = """\
name: test
files:
  editable: ["x.py"]
commands:
  run: "echo ok"
  eval: "echo 'metric: 1'"
metric:
  name: metric
  direction: minimize
agent:
  system_prompt: "custom_system.md"
"""
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(config_yaml)
    config = load_config(tmp_path)
    assert config.agent.system_prompt == "custom_system.md"


def test_system_prompt_default_none(tmp_path):
    config_yaml = """\
name: test
files:
  editable: ["x.py"]
commands:
  run: "echo ok"
  eval: "echo 'metric: 1'"
metric:
  name: metric
  direction: minimize
"""
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(config_yaml)
    config = load_config(tmp_path)
    assert config.agent.system_prompt is None


def test_allow_install_default_false(tmp_path):
    """Config without allow_install should default to False."""
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(MINIMAL_YAML)
    cfg = load_config(tmp_path)
    assert cfg.constraints.allow_install is False


def test_allow_install_parsed_from_yaml(tmp_path):
    """Config with allow_install: true should parse correctly."""
    yaml_with_install = MINIMAL_YAML.rstrip() + "\nconstraints:\n  allow_install: true\n"
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml_with_install)
    cfg = load_config(tmp_path)
    assert cfg.constraints.allow_install is True


def test_load_config_with_artifacts(tmp_path):
    """Config loads files.artifacts as a list of paths."""
    crucible_dir = tmp_path / ".crucible"
    crucible_dir.mkdir()
    (crucible_dir / "config.yaml").write_text(
        "name: test\n"
        "files:\n"
        "  editable: ['main.py']\n"
        "  artifacts: ['artifacts/', 'checkpoints/']\n"
        "commands:\n"
        "  run: 'echo ok'\n"
        "  eval: 'echo ok'\n"
        "metric:\n"
        "  name: score\n"
        "  direction: maximize\n"
    )
    (tmp_path / "main.py").touch()
    from crucible.config import load_config
    config = load_config(tmp_path)
    assert config.files.artifacts == ["artifacts/", "checkpoints/"]


def test_load_config_without_artifacts(tmp_path):
    """Config defaults artifacts to empty list when not specified."""
    crucible_dir = tmp_path / ".crucible"
    crucible_dir.mkdir()
    (crucible_dir / "config.yaml").write_text(
        "name: test\n"
        "files:\n"
        "  editable: ['main.py']\n"
        "commands:\n"
        "  run: 'echo ok'\n"
        "  eval: 'echo ok'\n"
        "metric:\n"
        "  name: score\n"
        "  direction: maximize\n"
    )
    (tmp_path / "main.py").touch()
    from crucible.config import load_config
    config = load_config(tmp_path)
    assert config.files.artifacts == []


def test_load_missing_required_fields(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("name: test\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_max_iterations_default_none(tmp_path):
    """Config without max_iterations defaults to None."""
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(MINIMAL_YAML)
    cfg = load_config(tmp_path)
    assert cfg.constraints.max_iterations is None


def test_max_iterations_parsed_from_yaml(tmp_path):
    """Config with max_iterations parses correctly."""
    yaml_with_max = MINIMAL_YAML.rstrip() + "\nconstraints:\n  max_iterations: 10\n"
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml_with_max)
    cfg = load_config(tmp_path)
    assert cfg.constraints.max_iterations == 10
