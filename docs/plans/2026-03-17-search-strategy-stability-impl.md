# Search Strategy & Stability Check — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add configurable search strategies (greedy/restart/beam) and auto-detect metric instability in `crucible validate`.

**Architecture:** Two independent features. Feature A enhances `crucible validate` to auto-detect stochastic metrics and write `evaluation.repeat` to config. Feature B adds a top-level `search:` config block and three search strategies: `greedy` (current default), `restart` (auto-backtrack on plateau), and `beam` (round-robin K-branch serial exploration with compact cross-beam context).

**Tech Stack:** Python 3.12, PyYAML, existing crucible modules (config, validator, orchestrator, git_manager, results, context, cli).

---

## Feature A: Stability Check

### Task 1: Add SearchConfig to config.py, migrate plateau_threshold

**Files:**
- Modify: `src/crucible/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing tests**

```python
# tests/test_config.py — add these tests

def test_search_config_defaults(tmp_path):
    """SearchConfig loads with defaults when search key absent."""
    (tmp_path / ".crucible").mkdir()
    (tmp_path / ".crucible" / "config.yaml").write_text(
        "name: test\nfiles:\n  editable: [sort.py]\n"
        "commands:\n  run: python sort.py\n  eval: python eval.py\n"
        "metric:\n  name: score\n  direction: maximize\n"
    )
    config = load_config(tmp_path)
    assert config.search.strategy == "greedy"
    assert config.search.beam_width == 3
    assert config.search.plateau_threshold == 8


def test_search_config_explicit(tmp_path):
    """SearchConfig reads explicit search block."""
    (tmp_path / ".crucible").mkdir()
    (tmp_path / ".crucible" / "config.yaml").write_text(
        "name: test\nfiles:\n  editable: [sort.py]\n"
        "commands:\n  run: python sort.py\n  eval: python eval.py\n"
        "metric:\n  name: score\n  direction: maximize\n"
        "search:\n  strategy: beam\n  beam_width: 2\n  plateau_threshold: 5\n"
    )
    config = load_config(tmp_path)
    assert config.search.strategy == "beam"
    assert config.search.beam_width == 2
    assert config.search.plateau_threshold == 5


def test_search_plateau_backward_compat(tmp_path):
    """constraints.plateau_threshold still works as fallback."""
    (tmp_path / ".crucible").mkdir()
    (tmp_path / ".crucible" / "config.yaml").write_text(
        "name: test\nfiles:\n  editable: [sort.py]\n"
        "commands:\n  run: python sort.py\n  eval: python eval.py\n"
        "metric:\n  name: score\n  direction: maximize\n"
        "constraints:\n  plateau_threshold: 12\n"
    )
    config = load_config(tmp_path)
    assert config.search.plateau_threshold == 12


def test_search_strategy_invalid(tmp_path):
    """Invalid strategy raises ConfigError."""
    (tmp_path / ".crucible").mkdir()
    (tmp_path / ".crucible" / "config.yaml").write_text(
        "name: test\nfiles:\n  editable: [sort.py]\n"
        "commands:\n  run: python sort.py\n  eval: python eval.py\n"
        "metric:\n  name: score\n  direction: maximize\n"
        "search:\n  strategy: ucb1\n"
    )
    with pytest.raises(ConfigError, match="search.strategy"):
        load_config(tmp_path)
```

**Step 2: Run tests to confirm failure**
```bash
uv run pytest tests/test_config.py::test_search_config_defaults tests/test_config.py::test_search_config_explicit tests/test_config.py::test_search_plateau_backward_compat tests/test_config.py::test_search_strategy_invalid -v
```
Expected: 4 FAILED (AttributeError: 'Config' object has no attribute 'search')

**Step 3: Add SearchConfig to config.py**

After `SandboxConfig` dataclass (line 90), add:

```python
@dataclass
class SearchConfig:
    strategy: str = "greedy"   # greedy | restart | beam
    beam_width: int = 3
    plateau_threshold: int = 8
```

Add to `Config` dataclass after `sandbox`:
```python
search: SearchConfig = field(default_factory=SearchConfig)
```

Add `_build_search` function before `load_config`:
```python
def _build_search(search_data: dict, constraints_data: dict) -> SearchConfig:
    plateau = search_data.get(
        "plateau_threshold",
        constraints_data.get("plateau_threshold", 8),
    )
    return SearchConfig(
        strategy=search_data.get("strategy", "greedy"),
        beam_width=search_data.get("beam_width", 3),
        plateau_threshold=plateau,
    )
```

In `load_config`, after `sandbox=_build_sandbox(...)`, add:
```python
        search=_build_search(
            raw.get("search", {}),
            raw.get("constraints", {}),
        ),
```

Add validation after the direction check:
```python
search_data = raw.get("search", {})
strategy = search_data.get("strategy", "greedy")
if strategy not in ("greedy", "restart", "beam"):
    raise ConfigError(
        f"search.strategy must be 'greedy', 'restart', or 'beam', got '{strategy}'"
    )
```

**Step 4: Run tests**
```bash
uv run pytest tests/test_config.py::test_search_config_defaults tests/test_config.py::test_search_config_explicit tests/test_config.py::test_search_plateau_backward_compat tests/test_config.py::test_search_strategy_invalid -v
```
Expected: 4 PASSED

**Step 5: Update context.py to use config.search.plateau_threshold**

In `src/crucible/context.py` line 368, change:
```python
# OLD
plateau = _plateau_hint(real_records, self.config.constraints.plateau_threshold)
# NEW
plateau = _plateau_hint(real_records, self.config.search.plateau_threshold)
```

**Step 6: Run all tests**
```bash
uv run pytest -x -q
```
Expected: all pass

**Step 7: Commit**
```bash
git add src/crucible/config.py src/crucible/context.py tests/test_config.py
git commit -m "feat: add SearchConfig with strategy/beam_width/plateau_threshold"
```

---

### Task 2: Stability check auto-writes repeat to config.yaml

**Files:**
- Modify: `src/crucible/validator.py`
- Test: `tests/test_validator.py`

**Step 1: Write the failing tests**

```python
# tests/test_validator.py — add these tests

from unittest.mock import patch, MagicMock
from crucible.validator import check_stability, run_stability_check_and_update
from crucible.runner import RunResult


def _make_config(tmp_path, repeat=1):
    """Helper: write a minimal config.yaml and return loaded Config."""
    (tmp_path / ".crucible").mkdir(exist_ok=True)
    (tmp_path / ".crucible" / "config.yaml").write_text(
        f"name: test\nfiles:\n  editable: [sort.py]\n"
        f"commands:\n  run: python sort.py\n  eval: python eval.py\n"
        f"metric:\n  name: score\n  direction: maximize\n"
        f"evaluation:\n  repeat: {repeat}\n"
    )
    from crucible.config import load_config
    return load_config(tmp_path)


def test_run_stability_check_stable(tmp_path):
    """Stable metric (CV < 5%): no config change, returns StabilityResult."""
    config = _make_config(tmp_path)
    ok_run = RunResult(exit_code=0, timed_out=False, stderr_tail="")
    with patch("crucible.validator.ExperimentRunner") as MockRunner:
        inst = MockRunner.return_value
        inst.execute.return_value = ok_run
        inst.parse_metric.return_value = 1.0
        result = run_stability_check_and_update(tmp_path, config, runs=3)

    assert result.stable is True
    # config.yaml should NOT be modified
    raw = (tmp_path / ".crucible" / "config.yaml").read_text()
    assert "repeat: 1" in raw or "repeat:" not in raw


def test_run_stability_check_unstable_writes_repeat(tmp_path):
    """Unstable metric (CV > 5%): auto-writes evaluation.repeat: 3 to config."""
    config = _make_config(tmp_path)
    ok_run = RunResult(exit_code=0, timed_out=False, stderr_tail="")
    values = [1.0, 1.2, 0.8]  # CV ≈ 20%
    with patch("crucible.validator.ExperimentRunner") as MockRunner:
        inst = MockRunner.return_value
        inst.execute.return_value = ok_run
        inst.parse_metric.side_effect = values
        result = run_stability_check_and_update(tmp_path, config, runs=3)

    assert result.stable is False
    assert result.cv > 5.0
    # config.yaml should now have repeat: 3
    from crucible.config import load_config
    updated = load_config(tmp_path)
    assert updated.evaluation.repeat == 3


def test_run_stability_check_writes_validated_marker(tmp_path):
    """Successful stability check writes .crucible/.validated marker."""
    config = _make_config(tmp_path)
    ok_run = RunResult(exit_code=0, timed_out=False, stderr_tail="")
    with patch("crucible.validator.ExperimentRunner") as MockRunner:
        inst = MockRunner.return_value
        inst.execute.return_value = ok_run
        inst.parse_metric.return_value = 1.0
        run_stability_check_and_update(tmp_path, config, runs=3)

    assert (tmp_path / ".crucible" / ".validated").exists()


def test_run_stability_check_already_repeat(tmp_path):
    """If repeat already > 1, skip stability check and return stable."""
    config = _make_config(tmp_path, repeat=3)
    result = run_stability_check_and_update(tmp_path, config, runs=3)
    assert result.stable is True
    assert result.reason == "repeat already configured"
```

**Step 2: Run tests to confirm failure**
```bash
uv run pytest tests/test_validator.py::test_run_stability_check_stable tests/test_validator.py::test_run_stability_check_unstable_writes_repeat tests/test_validator.py::test_run_stability_check_writes_validated_marker tests/test_validator.py::test_run_stability_check_already_repeat -v
```
Expected: 4 FAILED (ImportError: cannot import name 'run_stability_check_and_update')

**Step 3: Add `run_stability_check_and_update` to validator.py**

Add after `check_stability` function:

```python
def run_stability_check_and_update(
    project_root: Path, config, runs: int = 3
) -> StabilityResult:
    """Run stability check and auto-update config.yaml if metric is unstable.

    If evaluation.repeat is already > 1, skip and return stable.
    Writes .crucible/.validated marker on completion.
    """
    if config.evaluation.repeat > 1:
        return StabilityResult(stable=True, reason="repeat already configured")

    result = check_stability(project_root, config, runs=runs)

    if not result.stable:
        # Auto-update evaluation.repeat in config.yaml
        config_path = project_root / ".crucible" / "config.yaml"
        import yaml as _yaml
        with open(config_path) as f:
            raw = _yaml.safe_load(f)
        raw.setdefault("evaluation", {})["repeat"] = 3
        raw.setdefault("evaluation", {}).setdefault("aggregation", "median")
        with open(config_path, "w") as f:
            _yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)

    # Write validated marker (gitignored)
    marker = project_root / ".crucible" / ".validated"
    marker.write_text("")

    return result
```

**Step 4: Run tests**
```bash
uv run pytest tests/test_validator.py::test_run_stability_check_stable tests/test_validator.py::test_run_stability_check_unstable_writes_repeat tests/test_validator.py::test_run_stability_check_writes_validated_marker tests/test_validator.py::test_run_stability_check_already_repeat -v
```
Expected: 4 PASSED

**Step 5: Integrate into validate_project in validator.py**

In `validate_project`, after the "Eval/metric" check result (line ~112), call `run_stability_check_and_update` and append a `CheckResult`:

```python
    # 6. Stability check
    stability = run_stability_check_and_update(project_root, config, runs=3)
    if stability.reason == "repeat already configured":
        results.append(CheckResult(
            "Stability", True,
            f"evaluation.repeat={config.evaluation.repeat} already configured — skipping check"
        ))
    elif stability.stable:
        results.append(CheckResult(
            "Stability", True,
            f"CV={stability.cv:.1f}%  mean={stability.mean:.4f}  stdev={stability.stdev:.4f} ✓ stable"
        ))
    else:
        results.append(CheckResult(
            "Stability", True,  # not a failure, but a warning → auto-fixed
            f"CV={stability.cv:.1f}% ⚠ unstable — auto-set evaluation.repeat=3 in config.yaml"
        ))

    return results
```

**Step 6: Ensure `.validated` is gitignored**

In `orchestrator.py` `init()`, the gitignore lines list (around line 104) — add `.crucible/.validated`:
```python
needed = [p for p in ("results-*.jsonl", "run.log", "logs/", ".crucible/.validated") if p not in lines]
```

**Step 7: Run all tests**
```bash
uv run pytest -x -q
```

**Step 8: Commit**
```bash
git add src/crucible/validator.py src/crucible/orchestrator.py tests/test_validator.py
git commit -m "feat: stability check auto-writes repeat to config.yaml in crucible validate"
```

---

### Task 3: `crucible run` first-iteration hint when validate not run

**Files:**
- Modify: `src/crucible/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

Find the existing `run` command tests in `tests/test_cli.py` and add:

```python
def test_run_hints_validate_when_repeat_1_and_not_validated(tmp_path, capsys):
    """run prints validate tip when repeat=1 and .validated absent."""
    # Setup: minimal experiment with FakeAgent that immediately stops
    # ... use existing test patterns (setup_repo, make_config, FakeAgent mock)
    # After running 1 iteration, captured stdout should contain "crucible validate"
    from click.testing import CliRunner
    from crucible.cli import cli
    runner = CliRunner()
    # This test depends on project structure; adapt to existing test helpers
    # Key assertion:
    assert "crucible validate" in result.output
```

Note: Look at how `test_run_command` is structured in `tests/test_cli.py` and mirror it.

**Step 2: Run test to confirm failure**
```bash
uv run pytest tests/test_cli.py -k "hint" -v
```
Expected: FAILED

**Step 3: Add hint to cli.py `run` command**

In `cli.py`, inside the `run` command function, after `orch.init()` or `orch.resume()`, add:

```python
    # Hint: suggest validate if repeat=1 and not yet validated
    validated_marker = Path(project_dir) / ".crucible" / ".validated"
    if config.evaluation.repeat == 1 and not validated_marker.exists():
        click.echo(
            "Tip: Run 'crucible validate' first to check if your metric needs "
            "repeat runs (stochastic experiments may benefit from evaluation.repeat: 3)."
        )
```

**Step 4: Run tests**
```bash
uv run pytest tests/test_cli.py -k "hint" -v
```
Expected: PASSED

**Step 5: Run all tests**
```bash
uv run pytest -x -q
```

**Step 6: Commit**
```bash
git add src/crucible/cli.py tests/test_cli.py
git commit -m "feat: hint to run crucible validate when repeat=1 and not validated"
```

---

## Feature B: Search Strategies

### Task 4: git_manager.py — baseline commit tracking and beam branch support

**Files:**
- Modify: `src/crucible/git_manager.py`
- Test: `tests/test_git_manager.py` (create if not exists)

**Step 1: Write failing tests**

```python
# tests/test_git_manager.py

import pytest
from pathlib import Path
import subprocess
from crucible.git_manager import GitManager


def setup_git_repo(tmp_path: Path) -> GitManager:
    """Create a minimal git repo and return a GitManager for it."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "file.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return GitManager(workspace=tmp_path)


def test_get_head_returns_short_hash(tmp_path):
    gm = setup_git_repo(tmp_path)
    h = gm.head()
    assert len(h) == 7
    assert h.isalnum()


def test_reset_to_commit(tmp_path):
    """reset_to_commit hard-resets HEAD to target commit."""
    gm = setup_git_repo(tmp_path)
    baseline = gm.head()
    # Make another commit
    (tmp_path / "file.py").write_text("x = 2\n")
    gm.commit("second")
    assert gm.head() != baseline
    # Reset back
    gm.reset_to_commit(baseline)
    assert gm.head() == baseline


def test_create_beam_branches(tmp_path):
    """create_beam_branches creates N branches all pointing at same commit."""
    gm = setup_git_repo(tmp_path)
    baseline = gm.head()
    gm.create_branch("run1")
    gm.create_beam_branches("run1", beam_width=3)
    for i in range(3):
        branch = f"crucible/run1-beam-{i}"
        result = subprocess.run(
            ["git", "rev-parse", "--short", branch],
            cwd=tmp_path, capture_output=True, text=True
        )
        assert result.stdout.strip() == baseline


def test_checkout_beam(tmp_path):
    """checkout_beam switches to the correct beam branch."""
    gm = setup_git_repo(tmp_path)
    gm.create_branch("run1")
    gm.create_beam_branches("run1", beam_width=2)
    gm.checkout_beam("run1", 0)
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path, capture_output=True, text=True
    )
    assert result.stdout.strip() == "crucible/run1-beam-0"
```

**Step 2: Run tests to confirm failure**
```bash
uv run pytest tests/test_git_manager.py -v
```
Expected: `test_reset_to_commit`, `test_create_beam_branches`, `test_checkout_beam` FAILED

**Step 3: Add methods to git_manager.py**

```python
def reset_to_commit(self, commit: str) -> None:
    """Hard-reset HEAD to a specific commit (e.g., baseline)."""
    self._run("reset", "--hard", commit)

def create_beam_branches(self, tag: str, beam_width: int) -> None:
    """Create beam_width branches all starting at the current HEAD."""
    current = self.head()
    current_branch = self._run("branch", "--show-current")
    for i in range(beam_width):
        beam_branch = f"{self.branch_prefix}/{tag}-beam-{i}"
        # Create branch at current HEAD without checking out
        self._run("branch", beam_branch, current)
    # Stay on current branch

def checkout_beam(self, tag: str, beam_id: int) -> None:
    """Checkout the beam branch for the given beam_id."""
    branch_name = f"{self.branch_prefix}/{tag}-beam-{beam_id}"
    self._run("checkout", branch_name)
```

**Step 4: Run tests**
```bash
uv run pytest tests/test_git_manager.py -v
```
Expected: 4 PASSED

**Step 5: Commit**
```bash
git add src/crucible/git_manager.py tests/test_git_manager.py
git commit -m "feat: add reset_to_commit, create_beam_branches, checkout_beam to GitManager"
```

---

### Task 5: results.py — add beam_id field to ExperimentRecord

**Files:**
- Modify: `src/crucible/results.py`
- Test: `tests/test_results.py`

**Step 1: Write failing test**

```python
# tests/test_results.py — add:

def test_experiment_record_beam_id(tmp_path):
    """ExperimentRecord serializes and deserializes beam_id."""
    from crucible.results import ExperimentRecord, ResultsLog, results_filename
    log = ResultsLog(tmp_path / "results-test.jsonl")
    log.init()
    record = ExperimentRecord(
        commit="abc1234",
        metric_value=0.5,
        status="keep",
        description="test",
        beam_id=2,
    )
    log.log(record)
    records = log.read_all()
    assert records[0].beam_id == 2


def test_experiment_record_beam_id_defaults_none(tmp_path):
    """beam_id defaults to None for non-beam records."""
    from crucible.results import ExperimentRecord
    r = ExperimentRecord(commit="abc", metric_value=1.0, status="keep", description="x")
    assert r.beam_id is None
```

**Step 2: Run tests to confirm failure**
```bash
uv run pytest tests/test_results.py::test_experiment_record_beam_id tests/test_results.py::test_experiment_record_beam_id_defaults_none -v
```
Expected: FAILED

**Step 3: Add beam_id to ExperimentRecord**

In `results.py`, add field after `log_dir`:
```python
    beam_id: int | None = None
```

**Step 4: Run tests**
```bash
uv run pytest tests/test_results.py -v
```
Expected: all pass

**Step 5: Run all tests**
```bash
uv run pytest -x -q
```

**Step 6: Commit**
```bash
git add src/crucible/results.py tests/test_results.py
git commit -m "feat: add beam_id field to ExperimentRecord"
```

---

### Task 6: context.py — cross-beam history section

**Files:**
- Modify: `src/crucible/context.py`
- Test: `tests/test_context.py`

**Step 1: Write failing test**

```python
# tests/test_context.py — add:

def test_section_cross_beam_history_empty():
    """Returns empty string when no other beams provided."""
    from crucible.context import ContextAssembler
    # Need a minimal config; use existing make_config helper or inline
    # assembler._section_cross_beam_history([]) == ""
    pass  # implement after checking existing test structure


def test_section_cross_beam_history_compact(make_config_fixture):
    """Compact cross-beam section shows beam best + tried descriptions."""
    from crucible.context import ContextAssembler
    from crucible.results import ExperimentRecord, ResultsLog

    # Build two fake beam summaries
    beam_summaries = [
        {
            "beam_id": 1,
            "best": 1.823,
            "tried": [
                ExperimentRecord("a", 1.823, "keep", "attention heads×2"),
                ExperimentRecord("b", 2.1, "crash", "dropout 0.3"),
                ExperimentRecord("c", 1.9, "discard", "residual scaling"),
            ],
        },
        {
            "beam_id": 2,
            "best": 1.891,
            "tried": [
                ExperimentRecord("d", 1.891, "keep", "layer norm placement"),
                ExperimentRecord("e", 2.0, "discard", "weight tying"),
            ],
        },
    ]
    # Create assembler and call the new method
    # config = ... (use existing helper)
    # assembler = ContextAssembler(config, tmp_path, "crucible/run1-beam-0")
    # section = assembler._section_cross_beam_history(beam_summaries)
    # assert "beam-1" in section
    # assert "attention heads×2" in section
    # assert "beam-2" in section
    pass
```

Note: Adapt to existing test file structure. The key behavior to test:
- `_section_cross_beam_history([])` returns `""`
- With beam summaries, output contains beam IDs, best values, and description strings

**Step 2: Add `_section_cross_beam_history` to ContextAssembler**

In `context.py`, add method to `ContextAssembler`:

```python
def _section_cross_beam_history(self, beam_summaries: list[dict]) -> str:
    """Compact view of other beams' attempts (read-only context for current beam).

    beam_summaries: list of {beam_id: int, best: float, tried: list[ExperimentRecord]}
    """
    if not beam_summaries:
        return ""

    lines = ["## Other Beams (read-only — do NOT try approaches already tried there)"]
    for summary in beam_summaries:
        bid = summary["beam_id"]
        best = summary["best"]
        tried = summary["tried"]

        # Compact: "keep (desc) | crash (desc) | discard (desc)"
        parts = []
        for r in tried[-8:]:  # last 8 per beam
            symbol = {"keep": "✓", "crash": "💥", "discard": "✗"}.get(r.status, "?")
            parts.append(f"{symbol} {r.description}")

        tried_str = " | ".join(parts) if parts else "no attempts yet"
        lines.append(f"beam-{bid}  best={best}  tried: {tried_str}")

    return "\n".join(lines)
```

**Step 3: Update `assemble()` to accept optional beam_summaries**

Change signature of `assemble()`:
```python
def assemble(self, log: ResultsLog, beam_summaries: list[dict] | None = None) -> str:
```

Add cross-beam section to sections list (after `_section_state`, before `_section_history`):
```python
    cross_beam = self._section_cross_beam_history(beam_summaries or [])

    sections = [
        self._section_instructions(),
        self._section_state(real_records, best, summary),
        cross_beam,
        self._section_history(real_records),
        plateau,
        self._section_errors(),
        self._section_directive(),
    ]
```

**Step 4: Run all tests**
```bash
uv run pytest -x -q
```
Expected: all pass (new section is opt-in, defaults empty)

**Step 5: Commit**
```bash
git add src/crucible/context.py tests/test_context.py
git commit -m "feat: add cross-beam history section to ContextAssembler"
```

---

### Task 7: Orchestrator — restart and beam strategies

**Files:**
- Modify: `src/crucible/orchestrator.py`
- Test: `tests/test_integration.py`

**Step 1: Write failing tests**

```python
# tests/test_integration.py — add:

def test_restart_strategy_resets_to_baseline_on_plateau(tmp_path):
    """restart strategy resets HEAD to baseline when plateau_threshold reached."""
    # Setup a git repo with search.strategy: restart, plateau_threshold: 2
    # Use FakeAgent that always returns no improvement
    # After 2 consecutive non-improvements, HEAD should match baseline commit
    # config should have search.strategy = "restart", plateau_threshold = 2
    pass  # adapt to existing setup_repo/make_config/FakeAgent patterns


def test_beam_strategy_round_robins_branches(tmp_path):
    """beam strategy alternates between beam-0 and beam-1 branches."""
    # Setup with search.strategy: beam, beam_width: 2
    # After 2 iterations, each beam should have been visited once
    # Check branch names in git log
    pass
```

**Step 2: Run tests to confirm failure**
```bash
uv run pytest tests/test_integration.py -k "restart or beam" -v
```
Expected: FAILED

**Step 3: Add `_baseline_commit` tracking to Orchestrator.init()**

In `Orchestrator.init()`, after `self.git.create_branch(self.tag)` (or `create_branch_from`), capture the baseline:

```python
        self._baseline_commit = self.git.head()
```

Add `_baseline_commit: str = ""` to `__init__`.

**Step 4: Add plateau detection to run_loop()**

In `run_loop()`, after status is resolved and before checking consecutive failures, add plateau-triggered restart for `restart` strategy:

```python
                # Plateau check for restart strategy
                strategy = self.config.search.strategy
                plateau_threshold = self.config.search.plateau_threshold
                if strategy == "restart":
                    streak = self._count_plateau_streak()
                    if streak >= plateau_threshold:
                        logger.info(
                            f"[iter {self._iteration}] Plateau detected ({streak} iters) — "
                            "restarting from baseline"
                        )
                        self.git.reset_to_commit(self._baseline_commit)
                        self.context.add_error(
                            f"⟳ RESTART — {streak} iterations without improvement. "
                            "Returning to baseline. Your full history is preserved above. "
                            "Choose a completely different direction."
                        )
                        self._consecutive_failures = 0
```

Add helper method to `Orchestrator`:
```python
    def _count_plateau_streak(self) -> int:
        """Count consecutive non-keep records from the end of results."""
        records = self.results.read_all()
        streak = 0
        for r in reversed(records):
            if r.status == "keep":
                break
            streak += 1
        return streak
```

**Step 5: Add beam support**

Add `BeamState` dataclass before `Orchestrator`:
```python
@dataclass
class BeamState:
    beam_id: int
    results: "ResultsLog"
    context: "ContextAssembler"
    consecutive_failures: int = 0
    consecutive_skips: int = 0
    fail_seq: int = 0
    iteration: int = 0
```

Add `init_beams()` method to `Orchestrator`:
```python
    def init_beams(self) -> None:
        """Initialize beam branches and per-beam state. Call after init()."""
        from crucible.results import ResultsLog, results_filename
        beam_width = self.config.search.beam_width
        self.git.create_beam_branches(self.tag, beam_width)
        self._beams: list[BeamState] = []
        for i in range(beam_width):
            beam_branch = f"{self.config.git.branch_prefix}/{self.tag}-beam-{i}"
            beam_results = ResultsLog(
                self.workspace / f"results-{self.tag}-beam-{i}.jsonl"
            )
            beam_results.init()
            beam_context = ContextAssembler(
                config=self.config,
                project_root=self.workspace,
                branch_name=beam_branch,
            )
            self._beams.append(BeamState(
                beam_id=i,
                results=beam_results,
                context=beam_context,
            ))
        self._current_beam_idx = 0
```

Add `run_loop_beam()` method (replaces `run_loop` for beam strategy):
```python
    def run_loop_beam(self, max_iterations: int | None = None) -> None:
        """Beam search: round-robin across beam_width branches."""
        if max_iterations is None:
            max_iterations = self.config.constraints.max_iterations
        max_retries = self.config.constraints.max_retries
        session_count = 0

        try:
            while True:
                if max_iterations is not None and session_count >= max_iterations:
                    break

                # All beams exhausted?
                if all(b.consecutive_failures >= max_retries for b in self._beams):
                    logger.info("All beams exhausted consecutive failures — stopping.")
                    break

                # Pick next beam (round-robin, skip exhausted beams)
                beam = self._beams[self._current_beam_idx % len(self._beams)]
                self._current_beam_idx += 1
                if beam.consecutive_failures >= max_retries:
                    continue

                # Checkout beam branch
                self.git.checkout_beam(self.tag, beam.beam_id)

                # Build cross-beam summaries for OTHER beams
                other_summaries = [
                    {
                        "beam_id": b.beam_id,
                        "best": (
                            b.results.best(self.config.metric.direction).metric_value
                            if b.results.best(self.config.metric.direction) else None
                        ),
                        "tried": b.results.read_all(),
                    }
                    for b in self._beams if b.beam_id != beam.beam_id
                ]

                # Temporarily swap orchestrator state to this beam
                original_results = self.results
                original_context = self.context
                original_fail_seq = self._fail_seq
                original_consec_fail = self._consecutive_failures
                original_consec_skip = self._consecutive_skips
                original_iter = self._iteration

                self.results = beam.results
                self.context = beam.context
                self._fail_seq = beam.fail_seq
                self._consecutive_failures = beam.consecutive_failures
                self._consecutive_skips = beam.consecutive_skips
                self._iteration = beam.iteration

                # Inject cross-beam context
                beam.context._beam_summaries = other_summaries

                beam.iteration += 1
                status = self.run_one_iteration()
                session_count += 1

                # Update beam state from orchestrator state
                beam.fail_seq = self._fail_seq
                beam.consecutive_failures = self._consecutive_failures
                beam.consecutive_skips = self._consecutive_skips
                beam.iteration = self._iteration

                # Restore orchestrator state
                self.results = original_results
                self.context = original_context
                self._fail_seq = original_fail_seq
                self._consecutive_failures = original_consec_fail
                self._consecutive_skips = original_consec_skip
                self._iteration = original_iter

                best = beam.results.best(self.config.metric.direction)
                best_str = f"{best.metric_value}" if best else "N/A"
                logger.info(
                    f"[beam-{beam.beam_id} iter {beam.iteration}] {status} "
                    f"| best {self.config.metric.name}: {best_str}"
                )

        except KeyboardInterrupt:
            logger.info("Stopped.")
```

Update `context.py` `assemble()` to read `_beam_summaries` from assembler if set:
```python
    def assemble(self, log: ResultsLog, beam_summaries: list[dict] | None = None) -> str:
        if beam_summaries is None:
            beam_summaries = getattr(self, "_beam_summaries", None) or []
        # ... rest of method
```

**Step 6: Update run_loop() dispatch in Orchestrator**

Modify `run_loop()` to check strategy:
```python
    def run_loop(self, max_iterations: int | None = None) -> None:
        if self.config.search.strategy == "beam":
            self.run_loop_beam(max_iterations)
        else:
            self._run_loop_serial(max_iterations)
```

Rename current `run_loop` body to `_run_loop_serial`.

**Step 7: Update cli.py to call init_beams() for beam strategy**

In `cli.py` `run` command, after `orch.init()`:
```python
        if config.search.strategy == "beam":
            orch.init_beams()
```

**Step 8: Run integration tests**
```bash
uv run pytest tests/test_integration.py -x -v
```

**Step 9: Run all tests**
```bash
uv run pytest -x -q
```

**Step 10: Commit**
```bash
git add src/crucible/orchestrator.py src/crucible/cli.py tests/test_integration.py
git commit -m "feat: add restart and beam search strategies to orchestrator"
```

---

### Task 8: Docs update and beam results in results JSONL

**Files:**
- Modify: `docs/CONFIG.md`, `docs/FAQ.md`
- Modify: `src/crucible/orchestrator.py` (_make_record to include beam_id)
- Test: manual verification

**Step 1: Update _make_record to pass beam_id**

In the beam run loop, after `run_one_iteration`, the `_make_record` call inside `run_one_iteration` should include `beam_id`. The cleanest way: add `_current_beam_id: int | None = None` to `Orchestrator`, set it before `run_one_iteration` in the beam loop, and read it in `_make_record`:

```python
# In Orchestrator.__init__:
self._current_beam_id: int | None = None

# In run_loop_beam, before run_one_iteration:
self._current_beam_id = beam.beam_id

# In _make_record, add to ExperimentRecord:
beam_id=self._current_beam_id,
```

**Step 2: Update CONFIG.md**

Add `search` block documentation after the `constraints` section:

```markdown
## Search Strategy

Controls how crucible explores the optimization landscape.

```yaml
search:
  strategy: greedy        # greedy (default) | restart | beam
  beam_width: 3           # beam only: number of independent branches
  plateau_threshold: 8    # restart + beam: no-improvement iterations before acting
```

### `greedy` (default)

Always builds on the current best commit. Fast and efficient when the optimization
landscape is smooth. Risks getting stuck in local optima.

### `restart`

When `plateau_threshold` consecutive iterations produce no improvement, hard-resets
to the baseline commit and tries a completely different direction — with full history
preserved as context. Best for experiments with clear local optima.

### `beam`

Maintains `beam_width` independent branches, cycling through them in round-robin.
Each beam sees a compact summary of what other beams have tried, preventing
redundant exploration. Best when you have >50 iterations available.

Note: beam is still **serial** — one agent run at a time. Costs are proportional
to total iterations, not multiplied by beam_width.
```

**Step 3: Update FAQ.md**

Replace the DFS/local optima answer to mention search strategies:

Find the "local optima" section and update the escape hatch description to include:
```
The built-in escape hatch is the `search.strategy` config. Set `strategy: restart`
for automatic backtracking when stuck, or `strategy: beam` for systematic multi-path
exploration. Manual `--fork` is still available for ad-hoc exploration.
```

**Step 4: Run all tests one final time**
```bash
uv run pytest -q
```
Expected: all pass

**Step 5: Final commit**
```bash
git add docs/CONFIG.md docs/FAQ.md src/crucible/orchestrator.py
git commit -m "docs: document search strategies in CONFIG.md and FAQ.md"
```

---

## Summary

| Task | Feature | Key Change |
|------|---------|-----------|
| 1 | Stability A | `SearchConfig` in `config.py`, `plateau_threshold` migrated |
| 2 | Stability A | `run_stability_check_and_update` in `validator.py` |
| 3 | Stability A | First-iter hint in `cli.py` |
| 4 | Search B | `reset_to_commit`, `create_beam_branches`, `checkout_beam` in `git_manager.py` |
| 5 | Search B | `beam_id` field in `ExperimentRecord` |
| 6 | Search B | `_section_cross_beam_history` in `context.py` |
| 7 | Search B | `restart` plateau logic + `BeamState` + `run_loop_beam` in `orchestrator.py` |
| 8 | Search B | `beam_id` in records, CONFIG.md + FAQ.md docs |
