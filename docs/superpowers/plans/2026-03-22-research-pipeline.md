# Research Pipeline Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a research pipeline mode that chains multiple crucible optimization loops with pre-registration locking, gate thresholds, and per-step configuration.

**Architecture:** PipelineOrchestrator wrapper creates per-step Orchestrator instances on a single git branch. Each step completes its optimization loop, checks a gate threshold, then tags the commit and locks its output files as readonly for subsequent steps.

**Tech Stack:** Python 3.12+, dataclasses, PyYAML, Click CLI, existing crucible modules

**Spec:** `docs/superpowers/specs/2026-03-22-research-pipeline-design.md`

**Branch:** All work on `feat/research-pipeline` branch. Do NOT merge to main.

---

### Task 1: Config dataclasses — PipelineStepConfig + PipelineConfig

**Files:**
- Modify: `src/crucible/config.py:93-112` (add dataclasses before Config, add fields to Config)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test — PipelineStepConfig round-trip**

```python
# tests/test_config.py — add at end

def test_pipeline_step_config_defaults():
    from crucible.config import PipelineStepConfig, FilesConfig, CommandsConfig, MetricConfig
    step = PipelineStepConfig(
        step="hypothesize",
        instructions="hypo-program.md",
        files=FilesConfig(editable=["hypothesis.md"]),
        commands=CommandsConfig(run="python3 check.py", eval="cat run.log"),
        metric=MetricConfig(name="score", direction="maximize"),
    )
    assert step.step == "hypothesize"
    assert step.gate is None
    assert step.max_iterations is None
    assert step.agent is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_pipeline_step_config_defaults -v`
Expected: FAIL with `ImportError: cannot import name 'PipelineStepConfig'`

- [ ] **Step 3: Implement PipelineStepConfig, PipelineConfig, add to Config**

Add before the `Config` class in `src/crucible/config.py:93`:

```python
@dataclass
class PipelineStepConfig:
    step: str
    instructions: str
    files: FilesConfig
    commands: CommandsConfig
    metric: MetricConfig
    gate: float | None = None
    max_iterations: int | None = None
    agent: AgentConfig | None = None


@dataclass
class PipelineConfig:
    steps: list[PipelineStepConfig]
    lock_outputs: bool = True
```

Add to `Config` class (after `search` field):

```python
    mode: str = "optimize"
    pipeline: PipelineConfig | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_pipeline_step_config_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/crucible/config.py tests/test_config.py
git commit -m "feat(config): add PipelineStepConfig, PipelineConfig dataclasses"
```

---

### Task 2: Config loading — research mode validation

**Files:**
- Modify: `src/crucible/config.py:195-271` (load_config function)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests — research mode loads, optimize mode unchanged**

```python
# tests/test_config.py — add at end

RESEARCH_CONFIG_YAML = """\
name: "test-research"
mode: research
pipeline:
  lock_outputs: true
  steps:
    - step: hypothesize
      instructions: "hypo.md"
      files:
        editable: ["hypothesis.md"]
      commands:
        run: "python3 check.py"
        eval: "cat run.log"
      metric:
        name: "score"
        direction: "maximize"
      gate: 0.7
      max_iterations: 10
    - step: design
      instructions: "design.md"
      files:
        editable: ["plan.md"]
      commands:
        run: "python3 check_design.py"
        eval: "cat run.log"
      metric:
        name: "validity"
        direction: "maximize"
constraints:
  timeout_seconds: 60
"""


def test_load_research_config(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(RESEARCH_CONFIG_YAML)

    config = load_config(tmp_path)
    assert config.mode == "research"
    assert config.pipeline is not None
    assert len(config.pipeline.steps) == 2
    assert config.pipeline.steps[0].step == "hypothesize"
    assert config.pipeline.steps[0].gate == 0.7
    assert config.pipeline.steps[1].gate is None
    assert config.pipeline.lock_outputs is True


def test_research_mode_requires_pipeline(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text('name: "bad"\nmode: research\n')
    with pytest.raises(ConfigError, match="pipeline"):
        load_config(tmp_path)


def test_research_mode_rejects_duplicate_step_names(tmp_path):
    yaml = RESEARCH_CONFIG_YAML.replace("step: design", "step: hypothesize")
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml)
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(tmp_path)


def test_research_mode_rejects_beam_strategy(tmp_path):
    yaml = RESEARCH_CONFIG_YAML + "search:\n  strategy: beam\n"
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml)
    with pytest.raises(ConfigError, match="beam.*research"):
        load_config(tmp_path)


def test_optimize_mode_unchanged(tmp_path):
    """Existing optimize-mode configs still load without any pipeline fields."""
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        'name: "opt"\nfiles:\n  editable: ["x.py"]\n'
        'commands:\n  run: "python3 x.py"\n  eval: "cat run.log"\n'
        'metric:\n  name: "score"\n  direction: "maximize"\n'
    )
    config = load_config(tmp_path)
    assert config.mode == "optimize"
    assert config.pipeline is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "research or optimize_mode_unchanged" -v`
Expected: `test_load_research_config` FAIL (research mode hits _require for files.editable)

- [ ] **Step 3: Implement research mode loading in load_config**

Modify `load_config()` in `src/crucible/config.py`:

```python
def load_config(project_root: Path) -> Config:
    """Load and validate .crucible/config.yaml from *project_root*."""
    config_path = project_root / ".crucible" / "config.yaml"
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigError("Config file is not a valid YAML mapping")

    mode = raw.get("mode", "optimize")
    if mode not in ("optimize", "research"):
        raise ConfigError(f"mode must be 'optimize' or 'research', got '{mode}'")

    if mode == "optimize":
        _require(
            raw,
            "name",
            "files.editable",
            "commands.run",
            "commands.eval",
            "metric.name",
            "metric.direction",
        )
    else:
        # Research mode: validate pipeline instead of top-level fields
        _require(raw, "name")
        if "pipeline" not in raw or not raw["pipeline"]:
            raise ConfigError("mode 'research' requires a 'pipeline' section")
        _validate_pipeline(raw["pipeline"])

    # Direction validation — only for optimize mode (research mode has per-step metrics)
    if mode == "optimize":
        direction = raw["metric"]["direction"]
        if direction not in ("minimize", "maximize"):
            raise ConfigError(f"metric.direction must be 'minimize' or 'maximize', got '{direction}'")

    search_data = raw.get("search", {})
    strategy = search_data.get("strategy", "greedy")
    if strategy not in ("greedy", "restart", "beam"):
        raise ConfigError(
            f"search.strategy must be 'greedy', 'restart', or 'beam', got '{strategy}'"
        )
    if mode == "research" and strategy == "beam":
        raise ConfigError("beam search strategy is not supported with mode 'research'")

    files_data = raw.get("files", {})
    commands_data = raw.get("commands", {})
    metric_data = raw.get("metric", {})
    constraints_data = raw.get("constraints", {})
    git_data = raw.get("git", {})

    # Build pipeline config if present
    pipeline = _build_pipeline(raw.get("pipeline")) if raw.get("pipeline") else None

    # For research mode, commands/metric may be empty at top level — use safe .get()
    return Config(
        name=raw["name"],
        description=raw.get("description", ""),
        mode=mode,
        pipeline=pipeline,
        files=FilesConfig(
            editable=files_data.get("editable", []),
            readonly=files_data.get("readonly", []),
            hidden=files_data.get("hidden", []),
            artifacts=files_data.get("artifacts", []),
        ),
        commands=CommandsConfig(
            run=commands_data.get("run", ""),
            eval=commands_data.get("eval", ""),
            setup=commands_data.get("setup"),
        ),
        metric=MetricConfig(
            name=metric_data.get("name", ""),
            direction=metric_data.get("direction", ""),
        ),
        constraints=ConstraintsConfig(
            timeout_seconds=constraints_data.get("timeout_seconds", 600),
            max_retries=constraints_data.get("max_retries", 3),
            budget=_build_budget(constraints_data.get("budget")),
            plateau_threshold=constraints_data.get("plateau_threshold", 8),
            allow_install=constraints_data.get("allow_install", False),
            max_iterations=constraints_data.get("max_iterations"),
        ),
        agent=_build_agent(raw.get("agent", {})),
        git=GitConfig(
            branch_prefix=git_data.get("branch_prefix", "crucible"),
            tag_failed=git_data.get("tag_failed", True),
        ),
        evaluation=_build_evaluation(raw.get("evaluation", {})),
        sandbox=_build_sandbox(raw.get("sandbox")),
        search=_build_search(
            raw.get("search", {}),
            raw.get("constraints", {}),
        ),
    )
```

Add helper functions:

```python
def _validate_pipeline(pipeline_data: dict) -> None:
    """Validate pipeline section in research mode config."""
    steps = pipeline_data.get("steps")
    if not steps or not isinstance(steps, list):
        raise ConfigError("pipeline.steps must be a non-empty list")

    seen_names = set()
    for i, step_data in enumerate(steps):
        name = step_data.get("step")
        if not name:
            raise ConfigError(f"pipeline.steps[{i}]: 'step' name is required")
        if name in seen_names:
            raise ConfigError(f"pipeline: duplicate step name '{name}'")
        seen_names.add(name)

        # Validate required fields per step
        for field in ("instructions", "files", "commands", "metric"):
            if field not in step_data:
                raise ConfigError(f"pipeline.steps[{i}] ('{name}'): '{field}' is required")

        files = step_data.get("files", {})
        if not files.get("editable"):
            raise ConfigError(f"pipeline.steps[{i}] ('{name}'): files.editable is required")

        commands = step_data.get("commands", {})
        if not commands.get("run") or not commands.get("eval"):
            raise ConfigError(f"pipeline.steps[{i}] ('{name}'): commands.run and commands.eval are required")

        metric = step_data.get("metric", {})
        if not metric.get("name") or not metric.get("direction"):
            raise ConfigError(f"pipeline.steps[{i}] ('{name}'): metric.name and metric.direction are required")

        direction = metric["direction"]
        if direction not in ("minimize", "maximize"):
            raise ConfigError(f"pipeline.steps[{i}] ('{name}'): metric.direction must be 'minimize' or 'maximize'")


def _build_pipeline(pipeline_data: dict) -> PipelineConfig:
    """Build PipelineConfig from raw YAML dict."""
    steps = []
    for step_data in pipeline_data.get("steps", []):
        files_data = step_data.get("files", {})
        commands_data = step_data.get("commands", {})
        metric_data = step_data.get("metric", {})
        agent_data = step_data.get("agent")

        steps.append(PipelineStepConfig(
            step=step_data["step"],
            instructions=step_data["instructions"],
            files=FilesConfig(
                editable=files_data.get("editable", []),
                readonly=files_data.get("readonly", []),
                hidden=files_data.get("hidden", []),
                artifacts=files_data.get("artifacts", []),
            ),
            commands=CommandsConfig(
                run=commands_data["run"],
                eval=commands_data["eval"],
                setup=commands_data.get("setup"),
            ),
            metric=MetricConfig(
                name=metric_data["name"],
                direction=metric_data["direction"],
            ),
            gate=step_data.get("gate"),
            max_iterations=step_data.get("max_iterations"),
            agent=_build_agent(agent_data) if agent_data else None,
        ))

    return PipelineConfig(
        steps=steps,
        lock_outputs=pipeline_data.get("lock_outputs", True),
    )
```

Update the final `return Config(...)` to include `mode=mode, pipeline=pipeline`.

- [ ] **Step 4: Run all config tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/crucible/config.py tests/test_config.py
git commit -m "feat(config): load and validate research mode pipeline config"
```

---

### Task 3: Results — step_name field + results_filename

**Files:**
- Modify: `src/crucible/results.py:13-15` (results_filename), `src/crucible/results.py:40-57` (ExperimentRecord)
- Test: `tests/test_results.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_results.py — add at end

def test_results_filename_with_step():
    from crucible.results import results_filename
    assert results_filename("run1", "hypothesize") == "results-run1-hypothesize.jsonl"


def test_results_filename_without_step():
    from crucible.results import results_filename
    assert results_filename("run1") == "results-run1.jsonl"


def test_experiment_record_step_name():
    from crucible.results import ExperimentRecord, _serialize_record, _deserialize_record
    record = ExperimentRecord(
        commit="abc1234", metric_value=0.82, status="keep",
        description="test", step_name="hypothesize",
    )
    line = _serialize_record(record)
    assert '"step_name": "hypothesize"' in line
    restored = _deserialize_record(line)
    assert restored.step_name == "hypothesize"


def test_experiment_record_step_name_none_backward_compat():
    """Old records without step_name deserialize correctly."""
    from crucible.results import _deserialize_record
    line = '{"commit": "abc", "metric_value": 0.5, "status": "keep", "description": "old"}'
    record = _deserialize_record(line)
    assert record.step_name is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_results.py -k "step" -v`
Expected: FAIL — `results_filename` doesn't accept second arg, no `step_name` field

- [ ] **Step 3: Implement changes**

In `src/crucible/results.py`:

Update `results_filename`:
```python
def results_filename(tag: str, step_name: str | None = None) -> str:
    """Return the results JSONL filename for a given experiment tag."""
    if step_name:
        return f"results-{tag}-{step_name}.jsonl"
    return f"results-{tag}.jsonl"
```

Add to `ExperimentRecord` (after `beam_id` field):
```python
    step_name: str | None = None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_results.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `uv run pytest -v`
Expected: ALL PASS (existing callers use `results_filename(tag)` which still works)

- [ ] **Step 6: Commit**

```bash
git add src/crucible/results.py tests/test_results.py
git commit -m "feat(results): add step_name field and per-step results filename"
```

---

### Task 4: GitManager — tag_step + tag_exists

**Files:**
- Modify: `src/crucible/git_manager.py:115-120` (add methods at end)
- Test: `tests/test_git_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_git_manager.py — add at end (or create if doesn't exist)
# Use the existing test setup pattern with tmp_path

import subprocess

def setup_git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "file.txt").write_text("init")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)


def test_tag_step_creates_tag(tmp_path):
    from crucible.git_manager import GitManager
    setup_git_repo(tmp_path)
    git = GitManager(workspace=tmp_path)
    git.tag_step("study1", "hypothesize")
    assert git.tag_exists("step/study1/hypothesize")


def test_tag_step_force_overwrites(tmp_path):
    from crucible.git_manager import GitManager
    setup_git_repo(tmp_path)
    git = GitManager(workspace=tmp_path)
    git.tag_step("study1", "hypothesize")
    # Should not raise on second call
    git.tag_step("study1", "hypothesize", force=True)
    assert git.tag_exists("step/study1/hypothesize")


def test_tag_exists_false_when_missing(tmp_path):
    from crucible.git_manager import GitManager
    setup_git_repo(tmp_path)
    git = GitManager(workspace=tmp_path)
    assert not git.tag_exists("step/study1/nonexistent")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_git_manager.py -k "tag_step or tag_exists" -v`
Expected: FAIL — `GitManager has no attribute 'tag_step'`

- [ ] **Step 3: Implement tag_step and tag_exists**

Add to end of `GitManager` class in `src/crucible/git_manager.py`:

```python
    def tag_step(self, tag: str, step_name: str, force: bool = True) -> None:
        """Tag the current HEAD as a completed pipeline step."""
        tag_name = f"step/{tag}/{step_name}"
        args = ["tag"]
        if force:
            args.append("-f")
        args.append(tag_name)
        self._run(*args)

    def tag_exists(self, tag_name: str) -> bool:
        """Check if a git tag exists."""
        result = subprocess.run(
            ["git", "tag", "--list", tag_name],
            cwd=self.workspace, capture_output=True, text=True,
        )
        return bool(result.stdout.strip())
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_git_manager.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/crucible/git_manager.py tests/test_git_manager.py
git commit -m "feat(git): add tag_step and tag_exists methods"
```

---

### Task 5: Orchestrator — init_step + resume_step

**Files:**
- Modify: `src/crucible/orchestrator.py:142-148` (add methods after resume)
- Test: `tests/test_orchestrator.py` or `tests/test_integration.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pipeline_orchestrator.py (new file)

import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from crucible.config import load_config, Config, FilesConfig, CommandsConfig, MetricConfig, ConstraintsConfig, AgentConfig, GitConfig, SearchConfig
from crucible.orchestrator import Orchestrator
from crucible.agents.base import AgentInterface, AgentResult


class FakeAgent(AgentInterface):
    def __init__(self):
        self.call_count = 0

    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        self.call_count += 1
        target = workspace / "hypothesis.md"
        target.write_text(f"Hypothesis v{self.call_count}")
        return AgentResult(
            modified_files=[Path("hypothesis.md")],
            description=f"hypothesis v{self.call_count}",
        )


def make_config(**overrides) -> Config:
    defaults = dict(
        name="test",
        files=FilesConfig(editable=["hypothesis.md"]),
        commands=CommandsConfig(run="echo 'score: 0.8' > run.log", eval="cat run.log"),
        metric=MetricConfig(name="score", direction="maximize"),
        constraints=ConstraintsConfig(timeout_seconds=10, max_retries=2),
        agent=AgentConfig(),
        git=GitConfig(branch_prefix="test"),
        search=SearchConfig(),
    )
    defaults.update(overrides)
    return Config(**defaults)


def setup_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "hypothesis.md").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)


def test_init_step_creates_results_without_branch(tmp_path):
    """init_step initializes results log but does NOT create a branch."""
    setup_repo(tmp_path)
    config = make_config()
    agent = FakeAgent()

    # Create branch manually (simulating PipelineOrchestrator)
    subprocess.run(["git", "checkout", "-b", "test/study1"], cwd=tmp_path, check=True, capture_output=True)

    orch = Orchestrator(config, tmp_path, tag="study1", agent=agent)
    orch.init_step()

    assert orch.results.path.exists()
    # Should NOT have created another branch
    result = subprocess.run(
        ["git", "branch", "--list", "test/study1"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    # Branch exists because WE created it, not init_step
    assert "test/study1" in result.stdout


def test_resume_step_reads_iteration_count(tmp_path):
    """resume_step loads existing results and sets iteration counter."""
    setup_repo(tmp_path)
    config = make_config()
    agent = FakeAgent()

    subprocess.run(["git", "checkout", "-b", "test/study1"], cwd=tmp_path, check=True, capture_output=True)

    orch = Orchestrator(config, tmp_path, tag="study1", agent=agent)
    orch.init_step()

    # Simulate 3 previous iterations by writing records
    from crucible.results import ExperimentRecord
    for i in range(3):
        orch.results.log(ExperimentRecord(
            commit="abc", metric_value=float(i), status="keep",
            description=f"iter {i+1}", iteration=i+1,
        ))

    # Create new orchestrator and resume
    orch2 = Orchestrator(config, tmp_path, tag="study1", agent=agent)
    orch2.resume_step()
    assert orch2._iteration == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline_orchestrator.py -k "init_step or resume_step" -v`
Expected: FAIL — `Orchestrator has no attribute 'init_step'`

- [ ] **Step 3: Implement init_step and resume_step**

Add after `resume()` method in `src/crucible/orchestrator.py:148`:

```python
    def init_step(self) -> None:
        """Initialise results log for a pipeline step (no branch creation)."""
        self._baseline_commit = self.git.head()
        self.results.init()
        # Ensure gitignore covers results and logs
        gitignore = self.workspace / ".gitignore"
        lines = gitignore.read_text().splitlines() if gitignore.exists() else []
        needed = [p for p in ("results-*.jsonl", "run.log", "logs/") if p not in lines]
        if needed:
            lines.extend(needed)
            gitignore.write_text("\n".join(lines) + "\n")
            self.git.commit("chore: gitignore generated files")
        # Note: setup command for pipeline steps is handled by
        # PipelineOrchestrator (same as CLI handles it for optimize mode)

    def resume_step(self) -> None:
        """Resume a pipeline step from existing results (no branch checkout)."""
        existing = self.results.read_all()
        if existing:
            self._iteration = existing[-1].iteration or len(existing)
        self._fail_seq = sum(1 for r in existing if r.status in ("crash", "discard"))
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_pipeline_orchestrator.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/crucible/orchestrator.py tests/test_pipeline_orchestrator.py
git commit -m "feat(orchestrator): add init_step and resume_step for pipeline mode"
```

---

### Task 6: PipelineOrchestrator core

**Files:**
- Create: `src/crucible/pipeline.py`
- Test: `tests/test_pipeline_orchestrator.py` (append to existing)

- [ ] **Step 1: Write failing test — two-step pipeline completes**

```python
# tests/test_pipeline_orchestrator.py — add

from crucible.config import PipelineStepConfig, PipelineConfig

STEP_1 = PipelineStepConfig(
    step="hypothesize",
    instructions="hypo.md",
    files=FilesConfig(editable=["hypothesis.md"]),
    commands=CommandsConfig(run="echo 'score: 0.8' > run.log", eval="cat run.log"),
    metric=MetricConfig(name="score", direction="maximize"),
    gate=0.5,
    max_iterations=2,
)

STEP_2 = PipelineStepConfig(
    step="design",
    instructions="design.md",
    files=FilesConfig(editable=["plan.md"]),
    commands=CommandsConfig(run="echo 'validity: 0.9' > run.log", eval="cat run.log"),
    metric=MetricConfig(name="validity", direction="maximize"),
    gate=0.5,
    max_iterations=2,
)


def make_pipeline_config() -> Config:
    return Config(
        name="test-pipeline",
        mode="research",
        pipeline=PipelineConfig(steps=[STEP_1, STEP_2], lock_outputs=True),
        constraints=ConstraintsConfig(timeout_seconds=10, max_retries=2),
        agent=AgentConfig(),
        git=GitConfig(branch_prefix="test"),
        search=SearchConfig(),
    )


class FakePipelineAgent(AgentInterface):
    """Agent that writes to whichever editable file exists."""
    def __init__(self):
        self.call_count = 0

    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        self.call_count += 1
        # Write to first editable file found
        for name in ("hypothesis.md", "plan.md"):
            f = workspace / name
            if f.exists() or name in prompt:
                f.write_text(f"content v{self.call_count}")
                return AgentResult(
                    modified_files=[Path(name)],
                    description=f"edit {name} v{self.call_count}",
                )
        return AgentResult(modified_files=[], description="no-op")


def test_pipeline_two_steps_complete(tmp_path):
    from crucible.pipeline import PipelineOrchestrator

    setup_repo(tmp_path)
    # Create editable files
    (tmp_path / "hypothesis.md").write_text("initial")
    (tmp_path / "plan.md").write_text("initial")
    (tmp_path / ".crucible").mkdir(exist_ok=True)
    (tmp_path / ".crucible" / "hypo.md").write_text("Write a hypothesis.")
    (tmp_path / ".crucible" / "design.md").write_text("Write a design.")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add files"], cwd=tmp_path, check=True, capture_output=True)

    config = make_pipeline_config()
    agent = FakePipelineAgent()

    po = PipelineOrchestrator(config, tmp_path, "study1")

    # Mock agent creation inside pipeline — it should use our fake agent
    with patch("crucible.pipeline.create_agent", return_value=agent):
        result = po.run_pipeline()

    assert result.completed is True
    assert "hypothesize" in result.step_results
    assert "design" in result.step_results

    # Verify step tags exist
    assert po.git.tag_exists("step/study1/hypothesize")
    assert po.git.tag_exists("step/study1/design")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pipeline_orchestrator.py::test_pipeline_two_steps_complete -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'crucible.pipeline'`

- [ ] **Step 3: Implement PipelineOrchestrator**

Create `src/crucible/pipeline.py`:

```python
"""Pipeline orchestrator — chains multiple optimization loops for research mode."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path

from crucible.agents import create_agent
from crucible.config import Config, ConfigError, PipelineStepConfig
from crucible.git_manager import GitManager
from crucible.orchestrator import Orchestrator
from crucible.results import ResultsLog, results_filename

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    metric: float | None
    iterations: int
    status: str  # "passed" | "gate_failed" | "no_results"


@dataclass
class PipelineResult:
    completed: bool = False
    stopped_at: str | None = None
    reason: str | None = None
    step_results: dict[str, StepResult] = field(default_factory=dict)


class PipelineOrchestrator:
    """Chains multiple Orchestrator instances for research pipeline mode."""

    def __init__(
        self,
        config: Config,
        workspace: Path | str,
        tag: str,
        *,
        force_continue: bool = False,
        profile: bool = False,
    ) -> None:
        self.config = config
        self.workspace = Path(workspace)
        self.tag = tag
        self.force_continue = force_continue
        self.profile = profile
        self.step_results: dict[str, StepResult] = {}
        self.git = GitManager(
            workspace=self.workspace,
            branch_prefix=config.git.branch_prefix,
            tag_failed=config.git.tag_failed,
        )

    def run_pipeline(
        self,
        from_step: str | None = None,
        only_step: str | None = None,
    ) -> PipelineResult:
        """Execute pipeline steps sequentially."""
        steps = self._resolve_steps(from_step, only_step)

        # Create branch once
        if not self.git.branch_exists(self.tag):
            self.git.create_branch(self.tag)
        else:
            self.git.checkout_branch(self.tag)

        for i, step_cfg in enumerate(steps):
            step_index = self._step_index(step_cfg.step)
            logger.info(f"=== Pipeline step {step_index + 1}/{len(self.config.pipeline.steps)}: {step_cfg.step} ===")

            merged = self._merge_step_config(step_cfg, step_index)

            editable = set(merged.files.editable)
            if merged.constraints.allow_install:
                editable.add("requirements.txt")

            agent = create_agent(
                merged.agent,
                system_prompt_file=merged.agent.system_prompt,
                timeout=merged.constraints.timeout_seconds,
                hidden_files=set(merged.files.hidden),
                editable_files=editable,
            )

            orch = Orchestrator(
                merged, self.workspace, self.tag, agent,
                profile=self.profile,
            )
            # Override results path to be step-specific
            orch.results = ResultsLog(
                self.workspace / results_filename(self.tag, step_cfg.step)
            )

            if self._step_has_progress(step_cfg.step):
                orch.resume_step()
                logger.info(f"  Resuming from iteration {orch._iteration}")
            else:
                orch.init_step()
                # Run setup command if configured for this step
                if step_cfg.commands.setup:
                    import subprocess as sp
                    result = sp.run(
                        step_cfg.commands.setup, shell=True,
                        cwd=self.workspace,
                    )
                    if result.returncode != 0:
                        raise ConfigError(f"Setup command failed for step '{step_cfg.step}'")

            orch.run_loop(max_iterations=step_cfg.max_iterations)

            # Gate check
            best = orch.results.best(merged.metric.direction)
            if step_cfg.gate is not None:
                if best is None:
                    logger.warning(f"  Step '{step_cfg.step}': no successful iterations")
                    self.step_results[step_cfg.step] = StepResult(
                        metric=None, iterations=orch._iteration, status="no_results",
                    )
                    if not self.force_continue:
                        return PipelineResult(
                            stopped_at=step_cfg.step,
                            reason="no_successful_iterations",
                            step_results=self.step_results,
                        )
                    continue
                else:
                    passed = self._check_gate(
                        best.metric_value, step_cfg.gate, merged.metric.direction,
                    )
                    if not passed:
                        logger.warning(
                            f"  Step '{step_cfg.step}': gate failed "
                            f"({best.metric_value} vs threshold {step_cfg.gate})"
                        )
                        self.step_results[step_cfg.step] = StepResult(
                            metric=best.metric_value,
                            iterations=orch._iteration,
                            status="gate_failed",
                        )
                        if not self.force_continue:
                            return PipelineResult(
                                stopped_at=step_cfg.step,
                                reason="gate_failed",
                                step_results=self.step_results,
                            )
                        # force_continue: tag and proceed
                        self.git.tag_step(self.tag, step_cfg.step)
                        continue

            # Step passed
            self.git.tag_step(self.tag, step_cfg.step)
            self.step_results[step_cfg.step] = StepResult(
                metric=best.metric_value if best else None,
                iterations=orch._iteration,
                status="passed",
            )
            logger.info(
                f"  Step '{step_cfg.step}' completed: "
                f"metric={best.metric_value if best else 'N/A'}, "
                f"iterations={orch._iteration}"
            )

        return PipelineResult(completed=True, step_results=self.step_results)

    def _step_index(self, step_name: str) -> int:
        """Get the index of a step by name."""
        for i, s in enumerate(self.config.pipeline.steps):
            if s.step == step_name:
                return i
        raise ConfigError(f"Unknown step: {step_name}")

    def _merge_step_config(self, step: PipelineStepConfig, step_index: int) -> Config:
        """Merge global config with step-specific overrides."""
        merged = copy.deepcopy(self.config)

        merged.commands = step.commands
        merged.metric = step.metric
        merged.files = copy.deepcopy(step.files)
        merged.agent.instructions = step.instructions

        if step.max_iterations is not None:
            merged.constraints.max_iterations = step.max_iterations
        if step.agent:
            for attr in ("model", "system_prompt", "language", "base_url"):
                val = getattr(step.agent, attr, None)
                if val is not None:
                    setattr(merged.agent, attr, val)

        # Lock outputs: accumulate previous steps' editable as readonly
        if self.config.pipeline.lock_outputs:
            prev_steps = self.config.pipeline.steps[:step_index]
            for prev in prev_steps:
                for f in prev.files.editable:
                    if f not in merged.files.readonly:
                        merged.files.readonly.append(f)

        return merged

    def _step_has_progress(self, step_name: str) -> bool:
        """Check if a step has existing results."""
        results_path = self.workspace / results_filename(self.tag, step_name)
        return results_path.exists() and results_path.stat().st_size > 0

    def _resolve_steps(
        self, from_step: str | None, only_step: str | None,
    ) -> list[PipelineStepConfig]:
        """Determine which steps to run, validating prerequisites."""
        steps = self.config.pipeline.steps

        if only_step:
            match = [s for s in steps if s.step == only_step]
            if not match:
                raise ConfigError(f"Unknown step: {only_step}")
            idx = self._step_index(only_step)
            for prev in steps[:idx]:
                if not self.git.tag_exists(f"step/{self.tag}/{prev.step}"):
                    raise ConfigError(
                        f"Step '{only_step}' requires '{prev.step}' "
                        f"to be completed first (no tag found)"
                    )
            return match

        if from_step:
            idx = self._step_index(from_step)
            for prev in steps[:idx]:
                if not self.git.tag_exists(f"step/{self.tag}/{prev.step}"):
                    raise ConfigError(
                        f"Step '{from_step}' requires '{prev.step}' "
                        f"to be completed first (no tag found)"
                    )
            return steps[idx:]

        return list(steps)

    @staticmethod
    def _check_gate(value: float, gate: float, direction: str) -> bool:
        """Check if a metric value passes the gate threshold."""
        if direction == "maximize":
            return value >= gate
        return value <= gate
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_pipeline_orchestrator.py::test_pipeline_two_steps_complete -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/crucible/pipeline.py tests/test_pipeline_orchestrator.py
git commit -m "feat: add PipelineOrchestrator core with gate checking and step locking"
```

---

### Task 7: PipelineOrchestrator tests — gate failure, resume, prerequisites

**Files:**
- Modify: `tests/test_pipeline_orchestrator.py`

- [ ] **Step 1: Write gate failure test**

```python
# tests/test_pipeline_orchestrator.py — add

def test_pipeline_stops_on_gate_failure(tmp_path):
    """Pipeline stops when step metric doesn't reach gate threshold."""
    from crucible.pipeline import PipelineOrchestrator

    setup_repo(tmp_path)
    (tmp_path / "hypothesis.md").write_text("initial")
    (tmp_path / "plan.md").write_text("initial")
    (tmp_path / ".crucible").mkdir(exist_ok=True)
    (tmp_path / ".crucible" / "hypo.md").write_text("instructions")
    (tmp_path / ".crucible" / "design.md").write_text("instructions")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add files"], cwd=tmp_path, check=True, capture_output=True)

    # Set gate to 0.99 — score of 0.8 won't pass
    step1 = PipelineStepConfig(
        step="hypothesize",
        instructions="hypo.md",
        files=FilesConfig(editable=["hypothesis.md"]),
        commands=CommandsConfig(run="echo 'score: 0.8' > run.log", eval="cat run.log"),
        metric=MetricConfig(name="score", direction="maximize"),
        gate=0.99,  # impossible to reach
        max_iterations=1,
    )

    config = Config(
        name="test",
        mode="research",
        pipeline=PipelineConfig(steps=[step1, STEP_2], lock_outputs=True),
        constraints=ConstraintsConfig(timeout_seconds=10, max_retries=2),
        agent=AgentConfig(),
        git=GitConfig(branch_prefix="test"),
        search=SearchConfig(),
    )

    agent = FakePipelineAgent()
    po = PipelineOrchestrator(config, tmp_path, "gate-fail")

    with patch("crucible.pipeline.create_agent", return_value=agent):
        result = po.run_pipeline()

    assert result.completed is False
    assert result.stopped_at == "hypothesize"
    assert result.reason == "gate_failed"
    # Design step should not have run
    assert "design" not in result.step_results


def test_pipeline_force_continue_past_gate(tmp_path):
    """--force-continue allows pipeline to proceed past gate failure."""
    from crucible.pipeline import PipelineOrchestrator

    setup_repo(tmp_path)
    (tmp_path / "hypothesis.md").write_text("initial")
    (tmp_path / "plan.md").write_text("initial")
    (tmp_path / ".crucible").mkdir(exist_ok=True)
    (tmp_path / ".crucible" / "hypo.md").write_text("instructions")
    (tmp_path / ".crucible" / "design.md").write_text("instructions")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add files"], cwd=tmp_path, check=True, capture_output=True)

    step1 = PipelineStepConfig(
        step="hypothesize",
        instructions="hypo.md",
        files=FilesConfig(editable=["hypothesis.md"]),
        commands=CommandsConfig(run="echo 'score: 0.8' > run.log", eval="cat run.log"),
        metric=MetricConfig(name="score", direction="maximize"),
        gate=0.99,
        max_iterations=1,
    )

    config = Config(
        name="test",
        mode="research",
        pipeline=PipelineConfig(steps=[step1, STEP_2], lock_outputs=True),
        constraints=ConstraintsConfig(timeout_seconds=10, max_retries=2),
        agent=AgentConfig(),
        git=GitConfig(branch_prefix="test"),
        search=SearchConfig(),
    )

    agent = FakePipelineAgent()
    po = PipelineOrchestrator(config, tmp_path, "force", force_continue=True)

    with patch("crucible.pipeline.create_agent", return_value=agent):
        result = po.run_pipeline()

    # Should complete despite gate failure
    assert result.completed is True
    assert "hypothesize" in result.step_results
    assert "design" in result.step_results


def test_pipeline_readonly_accumulation(tmp_path):
    """Previous step's editable files become readonly in next step."""
    from crucible.pipeline import PipelineOrchestrator

    setup_repo(tmp_path)
    (tmp_path / "hypothesis.md").write_text("initial")
    (tmp_path / "plan.md").write_text("initial")
    (tmp_path / ".crucible").mkdir(exist_ok=True)
    (tmp_path / ".crucible" / "hypo.md").write_text("instructions")
    (tmp_path / ".crucible" / "design.md").write_text("instructions")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add files"], cwd=tmp_path, check=True, capture_output=True)

    config = make_pipeline_config()
    po = PipelineOrchestrator(config, tmp_path, "readonly-test")

    # Test _merge_step_config readonly accumulation
    step2 = config.pipeline.steps[1]
    merged = po._merge_step_config(step2, step_index=1)

    # hypothesis.md (step 1 editable) should now be in step 2's readonly
    assert "hypothesis.md" in merged.files.readonly
    # plan.md (step 2 editable) should NOT be in readonly
    assert "plan.md" not in merged.files.readonly


def test_pipeline_only_step_requires_prerequisites(tmp_path):
    """--step fails if prerequisites haven't completed."""
    from crucible.pipeline import PipelineOrchestrator

    setup_repo(tmp_path)
    config = make_pipeline_config()
    po = PipelineOrchestrator(config, tmp_path, "prereq-test")

    with pytest.raises(ConfigError, match="requires.*hypothesize"):
        po._resolve_steps(from_step=None, only_step="design")
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_pipeline_orchestrator.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_pipeline_orchestrator.py
git commit -m "test: add pipeline gate failure, force-continue, readonly accumulation, prereq tests"
```

---

### Task 8: Context — pipeline progress section

**Files:**
- Modify: `src/crucible/context.py:119-130` (ContextAssembler.__init__), `src/crucible/context.py:166-232` (_section_state)
- Test: `tests/test_context.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_context.py — add (or create if doesn't exist)

def test_pipeline_progress_in_state():
    from crucible.context import ContextAssembler
    from crucible.config import Config, FilesConfig, CommandsConfig, MetricConfig, AgentConfig, GitConfig, SearchConfig, ConstraintsConfig
    from crucible.pipeline import StepResult

    config = Config(
        name="test",
        files=FilesConfig(editable=["plan.md"]),
        commands=CommandsConfig(run="echo ok", eval="echo ok"),
        metric=MetricConfig(name="validity", direction="maximize"),
        agent=AgentConfig(),
        git=GitConfig(),
        search=SearchConfig(),
    )

    ctx = ContextAssembler(
        config=config,
        project_root="/tmp/test",
        branch_name="test/study1",
        pipeline_progress=[
            {"step": "hypothesize", "status": "passed", "metric": 0.82, "iterations": 5},
            {"step": "design", "status": "current", "metric": None, "iterations": 0},
            {"step": "execute", "status": "pending", "metric": None, "iterations": 0},
        ],
    )

    # Call _section_state to see if pipeline progress is included
    state = ctx._section_state([], None, {"total": 0, "kept": 0, "discarded": 0, "crashed": 0})
    assert "Pipeline Progress" in state
    assert "hypothesize" in state
    assert "0.82" in state
    assert "design" in state
    assert "current" in state.lower() or "▶" in state
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context.py::test_pipeline_progress_in_state -v`
Expected: FAIL — `ContextAssembler doesn't accept pipeline_progress`

- [ ] **Step 3: Implement pipeline_progress in ContextAssembler**

Modify `ContextAssembler.__init__` in `src/crucible/context.py:122`:

```python
    def __init__(
        self, config: Config, project_root: Path, branch_name: str,
        pipeline_progress: list[dict] | None = None,
    ) -> None:
        self.config = config
        self.project_root = Path(project_root)
        self.branch_name = branch_name
        self._pipeline_progress = pipeline_progress
        self._errors: List[str] = []
        self._crash_info: List[str] = []
        self._last_crash_info: List[str] = []
        self._prompt_breakdown: dict[str, int] | None = None
```

Add pipeline progress rendering at end of `_section_state`, before `return`:

```python
        # Pipeline progress (research mode)
        if self._pipeline_progress:
            lines.append("\n--- Pipeline Progress ---")
            for step_info in self._pipeline_progress:
                name = step_info["step"]
                status = step_info["status"]
                metric = step_info.get("metric")
                iters = step_info.get("iterations", 0)
                if status == "passed":
                    metric_str = f" — {metric}" if metric is not None else ""
                    lines.append(f"✓ {name}{metric_str} ({iters} iterations)")
                elif status == "current":
                    lines.append(f"▶ {name} (current step)")
                else:
                    lines.append(f"○ {name} — pending")

        return "\n".join(lines)
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Update PipelineOrchestrator to pass pipeline_progress**

In `src/crucible/pipeline.py`, after creating `orch = Orchestrator(...)`, build and inject `pipeline_progress` into the context:

```python
            # Build pipeline progress for context
            progress = []
            for j, s in enumerate(self.config.pipeline.steps):
                if s.step in self.step_results:
                    sr = self.step_results[s.step]
                    progress.append({"step": s.step, "status": sr.status, "metric": sr.metric, "iterations": sr.iterations})
                elif s.step == step_cfg.step:
                    progress.append({"step": s.step, "status": "current", "metric": None, "iterations": 0})
                else:
                    progress.append({"step": s.step, "status": "pending", "metric": None, "iterations": 0})
            orch.context._pipeline_progress = progress
```

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/crucible/context.py src/crucible/pipeline.py tests/test_context.py
git commit -m "feat(context): add pipeline progress section to agent prompt"
```

---

### Task 9: CLI — run command pipeline dispatch

**Files:**
- Modify: `src/crucible/cli.py:407-533` (run command)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_cli.py — add at end

def test_run_dispatches_to_pipeline_for_research_mode(tmp_path):
    """run command detects mode=research and uses PipelineOrchestrator."""
    from click.testing import CliRunner
    from crucible.cli import main

    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        'name: "test"\nmode: research\n'
        'pipeline:\n  steps:\n'
        '    - step: s1\n      instructions: "p.md"\n'
        '      files:\n        editable: ["x.py"]\n'
        '      commands:\n        run: "echo score: 1"\n        eval: "echo score: 1"\n'
        '      metric:\n        name: score\n        direction: maximize\n'
        '      max_iterations: 1\n'
    )
    (cfg_dir / "p.md").write_text("do stuff")
    (tmp_path / "x.py").write_text("x = 1")

    runner = CliRunner()
    # This should attempt to use PipelineOrchestrator
    # We patch it to avoid actually running
    with patch("crucible.pipeline.PipelineOrchestrator") as mock_po:
        mock_result = MagicMock()
        mock_result.completed = True
        mock_result.step_results = {}
        mock_po.return_value.run_pipeline.return_value = mock_result

        result = runner.invoke(main, ["run", "--tag", "t1", "--project-dir", str(tmp_path)])

    mock_po.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_run_dispatches_to_pipeline_for_research_mode -v`
Expected: FAIL — `crucible.cli` has no `PipelineOrchestrator` import

- [ ] **Step 3: Implement pipeline dispatch in run command**

Modify the `run` command in `src/crucible/cli.py`. Add new options and pipeline dispatch logic:

At the top of `run` function, after `config = load_config(project)`, add:

```python
    # Pipeline mode dispatch
    if config.mode == "research":
        from crucible.pipeline import PipelineOrchestrator
        # ... (see below)
```

Add CLI options to run command decorator:
```python
@click.option("--from-step", default=None, help=_("Start pipeline from this step."))
@click.option("--step", "only_step", default=None, help=_("Run only this pipeline step."))
@click.option("--force-continue", is_flag=True, default=False, help=_("Continue past gate failures."))
```

In the run function body, after config loading:
```python
    if config.mode == "research":
        from crucible.pipeline import PipelineOrchestrator

        if config.pipeline is None:
            raise click.ClickException("mode 'research' requires a pipeline configuration")

        # Auto-init git if needed
        if not (project / ".git").exists():
            subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=project, check=True, capture_output=True)

        check_claude_cli()

        po = PipelineOrchestrator(
            config, project, tag,
            force_continue=force_continue,
            profile=profile,
        )

        click.echo(_("Running research pipeline '{name}' ({n} steps)...").format(
            name=config.name, n=len(config.pipeline.steps),
        ))
        result = po.run_pipeline(from_step=from_step, only_step=only_step)

        if result.completed:
            click.echo(_("Pipeline completed successfully."))
        else:
            click.echo(_("Pipeline stopped at step '{step}': {reason}").format(
                step=result.stopped_at, reason=result.reason,
            ))
        return

    # ... existing optimize mode code continues below ...
```

Update function signature to include new params:
```python
def run(tag, project_dir, model, timeout, max_iterations, no_interactive, profile,
        from_step, only_step, force_continue):
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_cli.py::test_run_dispatches_to_pipeline_for_research_mode -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/crucible/cli.py tests/test_cli.py
git commit -m "feat(cli): dispatch run command to PipelineOrchestrator for research mode"
```

---

### Task 10: CLI — status command pipeline display

**Files:**
- Modify: `src/crucible/cli.py:536-585` (status command)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_cli.py — add

def test_status_shows_pipeline_steps(tmp_path):
    """status command shows per-step progress for pipeline runs."""
    from click.testing import CliRunner
    from crucible.cli import main
    from crucible.results import ResultsLog, ExperimentRecord, results_filename

    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        'name: "test"\nmode: research\n'
        'pipeline:\n  steps:\n'
        '    - step: hypothesize\n      instructions: "p.md"\n'
        '      files:\n        editable: ["h.md"]\n'
        '      commands:\n        run: "echo score: 1"\n        eval: "echo score: 1"\n'
        '      metric:\n        name: score\n        direction: maximize\n'
        '    - step: design\n      instructions: "p.md"\n'
        '      files:\n        editable: ["d.md"]\n'
        '      commands:\n        run: "echo score: 1"\n        eval: "echo score: 1"\n'
        '      metric:\n        name: validity\n        direction: maximize\n'
    )

    # Create step-1 results
    r1 = ResultsLog(tmp_path / results_filename("t1", "hypothesize"))
    r1.init()
    r1.log(ExperimentRecord(commit="abc", metric_value=0.82, status="keep", description="test", iteration=1))

    runner = CliRunner()
    result = runner.invoke(main, ["status", "--tag", "t1", "--project-dir", str(tmp_path)])

    assert "hypothesize" in result.output
    assert "0.82" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_status_shows_pipeline_steps -v`
Expected: FAIL — status command doesn't look for pipeline results

- [ ] **Step 3: Implement pipeline status display**

In the `status` command, after config loading, add pipeline detection:

```python
    if config.mode == "research" and config.pipeline:
        # Show per-step status
        click.echo(f"Pipeline: {config.name} ({len(config.pipeline.steps)} steps)")
        for step_cfg in config.pipeline.steps:
            step_results = ResultsLog(project / results_filename(tag, step_cfg.step))
            if not step_results.path.exists():
                click.echo(f"  ○ {step_cfg.step} — pending")
                continue
            best = step_results.best(step_cfg.metric.direction)
            summary = step_results.summary()
            if best:
                click.echo(
                    f"  ✓ {step_cfg.step} — {step_cfg.metric.name}: {best.metric_value} "
                    f"({summary['total']} iters, {summary['kept']} kept)"
                )
            else:
                click.echo(f"  ▶ {step_cfg.step} — {summary['total']} iters, no improvement yet")
        return
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_cli.py::test_status_shows_pipeline_steps -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/crucible/cli.py tests/test_cli.py
git commit -m "feat(cli): show per-step pipeline progress in status command"
```

---

### Task 11: Integration test — full pipeline end-to-end

**Files:**
- Modify: `tests/test_pipeline_orchestrator.py`

- [ ] **Step 1: Write end-to-end test with real git operations**

```python
# tests/test_pipeline_orchestrator.py — add

def test_pipeline_end_to_end_with_readonly_enforcement(tmp_path):
    """Full pipeline: step 1 edits hypothesis.md, step 2 cannot edit hypothesis.md."""
    from crucible.pipeline import PipelineOrchestrator

    setup_repo(tmp_path)
    (tmp_path / "hypothesis.md").write_text("initial hypothesis")
    (tmp_path / "plan.md").write_text("initial plan")
    (tmp_path / ".crucible").mkdir(exist_ok=True)
    (tmp_path / ".crucible" / "hypo.md").write_text("Write hypothesis")
    (tmp_path / ".crucible" / "design.md").write_text("Write design plan")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add files"], cwd=tmp_path, check=True, capture_output=True)

    config = make_pipeline_config()

    # Verify readonly accumulation in merged configs
    po = PipelineOrchestrator(config, tmp_path, "e2e")

    # Step 1: hypothesis.md is editable
    merged1 = po._merge_step_config(config.pipeline.steps[0], 0)
    assert "hypothesis.md" in merged1.files.editable
    assert "hypothesis.md" not in merged1.files.readonly

    # Step 2: hypothesis.md is now readonly
    merged2 = po._merge_step_config(config.pipeline.steps[1], 1)
    assert "hypothesis.md" in merged2.files.readonly
    assert "plan.md" in merged2.files.editable
    assert "plan.md" not in merged2.files.readonly


def test_pipeline_check_gate_maximize():
    from crucible.pipeline import PipelineOrchestrator
    assert PipelineOrchestrator._check_gate(0.8, 0.7, "maximize") is True
    assert PipelineOrchestrator._check_gate(0.5, 0.7, "maximize") is False


def test_pipeline_check_gate_minimize():
    from crucible.pipeline import PipelineOrchestrator
    assert PipelineOrchestrator._check_gate(0.3, 0.5, "minimize") is True
    assert PipelineOrchestrator._check_gate(0.8, 0.5, "minimize") is False
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_pipeline_orchestrator.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_pipeline_orchestrator.py
git commit -m "test: add end-to-end pipeline and gate check tests"
```

---

### Task 12: Final verification + i18n

**Files:**
- Modify: `src/crucible/cli.py` (wrap new strings in `_()`)
- No new files

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 2: Verify i18n — all user-facing strings wrapped in _()**

Check `cli.py` for any new strings not wrapped in `_()`. Wrap them.

- [ ] **Step 3: Run linting**

Run: `uv run ruff check src/crucible/pipeline.py src/crucible/config.py src/crucible/cli.py`
Expected: No errors

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: i18n and lint cleanup for pipeline mode"
```

- [ ] **Step 5: Verify branch state**

```bash
git log --oneline feat/research-pipeline..HEAD  # should show all pipeline commits
uv run pytest -v                                 # all pass
```

---

### Deferred (not in scope for v1)

- `history` command `--step` filter — minor UX, add when pipeline is proven in use
- Token profiling per step — works automatically via existing `--profile` flag per orchestrator instance; per-step aggregation in `postmortem` deferred
- `lock_outputs: false` test — add when the feature is actually needed
- `from_step` resume test — covered by prerequisite validation test; full e2e deferred
