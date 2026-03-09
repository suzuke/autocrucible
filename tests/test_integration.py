import subprocess
import pytest
from pathlib import Path
from crucible.config import load_config
from crucible.orchestrator import Orchestrator
from crucible.agents.base import AgentInterface, AgentResult


class FakeAgent(AgentInterface):
    """Agent that makes a deterministic edit each iteration."""
    def __init__(self):
        self.call_count = 0

    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        self.call_count += 1
        train = workspace / "train.py"
        train.write_text(f'x = {self.call_count}\nprint(f"score: {{x}}")')
        return AgentResult(
            modified_files=[Path("train.py")],
            description=f"set x to {self.call_count}",
        )


CONFIG_YAML = """\
name: "integration-test"
files:
  editable: ["train.py"]
  readonly: ["data.py"]
commands:
  run: "python3 train.py > run.log 2>&1"
  eval: "cat run.log"
metric:
  name: "score"
  direction: "maximize"
constraints:
  timeout_seconds: 10
  max_retries: 2
agent:
  type: "fake"
  instructions: "program.md"
git:
  branch_prefix: "test"
  tag_failed: true
"""


def setup_integration_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "train.py").write_text('x = 0\nprint(f"score: {x}")')
    (tmp_path / "data.py").write_text("# readonly data")
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(CONFIG_YAML)
    (cfg_dir / "program.md").write_text("Maximize the score by changing x.")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)


def test_three_iterations(tmp_path):
    setup_integration_repo(tmp_path)
    config = load_config(tmp_path)
    agent = FakeAgent()

    orch = Orchestrator(config, tmp_path, tag="integ", agent=agent)
    orch.init()

    results = []
    for _ in range(3):
        status = orch.run_one_iteration()
        results.append(status)

    # All should be keep (score increases each time: 1, 2, 3)
    assert results[0] == "keep"
    assert results[1] == "keep"
    assert results[2] == "keep"

    all_records = orch.results.read_all()
    assert len(all_records) == 3
    assert all_records[0].metric_value == 1.0
    assert all_records[1].metric_value == 2.0
    assert all_records[2].metric_value == 3.0
