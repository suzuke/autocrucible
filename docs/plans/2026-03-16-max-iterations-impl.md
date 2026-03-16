# --max-iterations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `--max-iterations` CLI flag and config option to bound the number of experiment iterations.

**Architecture:** Add `max_iterations` field to `ConstraintsConfig`, pass CLI override through to `Orchestrator.run_loop()`, which checks a local session counter against the limit each iteration.

**Tech Stack:** Python, Click, pytest

---

### Task 1: Config — add max_iterations field and parsing

**Files:**
- Modify: `src/crucible/config.py:62-67` (ConstraintsConfig dataclass)
- Modify: `src/crucible/config.py:222-228` (constraints parsing in load_config)
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_max_iterations_default_none tests/test_config.py::test_max_iterations_parsed_from_yaml -v`
Expected: FAIL with `AttributeError: ... has no attribute 'max_iterations'`

**Step 3: Write minimal implementation**

In `src/crucible/config.py`, add field to `ConstraintsConfig`:

```python
@dataclass
class ConstraintsConfig:
    timeout_seconds: int = 600
    max_retries: int = 3
    budget: BudgetConfig | None = None
    plateau_threshold: int = 8
    allow_install: bool = False
    max_iterations: int | None = None
```

In `load_config()`, parse the field (in the `ConstraintsConfig(...)` constructor):

```python
max_iterations=constraints_data.get("max_iterations"),
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/crucible/config.py tests/test_config.py
git commit -m "feat: add max_iterations to ConstraintsConfig"
```

---

### Task 2: Orchestrator — run_loop accepts max_iterations parameter

**Files:**
- Modify: `src/crucible/orchestrator.py:332-360` (run_loop method)
- Test: `tests/test_orchestrator.py`

**Step 1: Write the failing test**

Add to `tests/test_orchestrator.py`:

```python
def test_run_loop_stops_at_max_iterations(tmp_path):
    """run_loop stops after max_iterations iterations."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    call_count = 0

    def modify_file(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        (tmp_path / "train.py").write_text(f"x = {call_count}")
        return AgentResult(modified_files=[Path("train.py")], description=f"iter {call_count}")
    mock_agent.generate_edit.side_effect = modify_file

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.return_value = 0.95 - call_count * 0.1
        orch.run_loop(max_iterations=3)

    assert call_count == 3


def test_run_loop_none_max_iterations_uses_config(tmp_path):
    """run_loop with max_iterations=None falls back to config value."""
    setup_repo(tmp_path)
    cfg = make_config()
    cfg.constraints.max_iterations = 2
    mock_agent = MagicMock()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch.init()

    call_count = 0

    def modify_file(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        (tmp_path / "train.py").write_text(f"x = {call_count}")
        return AgentResult(modified_files=[Path("train.py")], description=f"iter {call_count}")
    mock_agent.generate_edit.side_effect = modify_file

    with patch.object(orch.runner, "execute") as mock_exec, \
         patch.object(orch.runner, "parse_metric") as mock_parse:
        mock_exec.return_value = MagicMock(exit_code=0, timed_out=False, stderr_tail="")
        mock_parse.return_value = 0.95 - call_count * 0.1
        orch.run_loop()  # No explicit max_iterations — should use config

    assert call_count == 2
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py::test_run_loop_stops_at_max_iterations tests/test_orchestrator.py::test_run_loop_none_max_iterations_uses_config -v`
Expected: FAIL — `run_loop()` doesn't accept `max_iterations` parameter, and runs forever (or until mock exhaustion)

**Step 3: Write minimal implementation**

In `src/crucible/orchestrator.py`, modify `run_loop`:

```python
def run_loop(self, max_iterations: int | None = None) -> None:
    """Run iterations until stopped, budget exceeded, or max_iterations reached."""
    if max_iterations is None:
        max_iterations = self.config.constraints.max_iterations

    max_retries = self.config.constraints.max_retries
    session_count = 0
    try:
        while True:
            if max_iterations is not None and session_count >= max_iterations:
                logger.info(f"Reached max iterations ({max_iterations}), stopping.")
                break

            logger.info(f"--- iter {self._iteration + 1} ---")
            status = self.run_one_iteration()
            session_count += 1

            best = self.results.best(self.config.metric.direction)
            best_str = f"{best.metric_value}" if best else "N/A"
            logger.info(f"[iter {self._iteration}] {status} | best {self.config.metric.name}: {best_str}")

            if status == "budget_exceeded":
                logger.warning("Budget limit reached, stopping.")
                break

            if status in ("skip", "violation"):
                self._consecutive_skips += 1
            else:
                self._consecutive_skips = 0

            if self._consecutive_failures >= max_retries:
                logger.warning(f"[iter {self._iteration}] {max_retries} consecutive failures, stopping.")
                break
            if self._consecutive_skips >= max_retries:
                logger.warning(f"[iter {self._iteration}] {max_retries} consecutive skips, stopping.")
                break
    except KeyboardInterrupt:
        logger.info(f"Stopped after {self._iteration} iterations.")
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/crucible/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: run_loop accepts max_iterations parameter"
```

---

### Task 3: CLI — add --max-iterations option to run command

**Files:**
- Modify: `src/crucible/cli.py:405-508` (run command)
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_run_max_iterations_flag(tmp_path):
    """--max-iterations flag is passed to run_loop."""
    setup_project(tmp_path)
    runner = CliRunner()
    with patch("crucible.orchestrator.Orchestrator.run_loop") as mock_loop:
        result = runner.invoke(
            main,
            ["run", "--tag", "mi1", "--max-iterations", "5", "--project-dir", str(tmp_path)],
        )
    assert result.exit_code == 0, result.output
    mock_loop.assert_called_once_with(max_iterations=5)


def test_run_without_max_iterations_passes_none(tmp_path):
    """Without --max-iterations, run_loop is called with max_iterations=None."""
    setup_project(tmp_path)
    runner = CliRunner()
    with patch("crucible.orchestrator.Orchestrator.run_loop") as mock_loop:
        result = runner.invoke(
            main,
            ["run", "--tag", "mi2", "--project-dir", str(tmp_path)],
        )
    assert result.exit_code == 0, result.output
    mock_loop.assert_called_once_with(max_iterations=None)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_run_max_iterations_flag tests/test_cli.py::test_run_without_max_iterations_passes_none -v`
Expected: FAIL — `No such option: --max-iterations`

**Step 3: Write minimal implementation**

In `src/crucible/cli.py`, add the option to the `run` command decorator and pass it through:

```python
@main.command()
@click.option("--tag", required=True, help="Experiment tag / branch suffix.")
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--model", default=None, help="Claude model to use (e.g. sonnet, opus).")
@click.option("--timeout", default=600, type=int, help="Agent timeout per iteration (seconds).")
@click.option("--max-iterations", default=None, type=int, help="Maximum iterations to run (default: unlimited).")
@click.option("--no-interactive", is_flag=True, default=False, help="Skip interactive prompts (start fresh).")
@_verbose_option
def run(tag: str, project_dir: str, model: str | None, timeout: int, max_iterations: int | None, no_interactive: bool) -> None:
```

Then change the `orch.run_loop()` call at line 507:

```python
orch.run_loop(max_iterations=max_iterations)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/crucible/cli.py tests/test_cli.py
git commit -m "feat: add --max-iterations CLI flag to crucible run"
```

---

### Task 4: Update scaffold template and help text

**Files:**
- Modify: `src/crucible/cli.py:198-211` (scaffold config.yaml template — add commented max_iterations example)

**Step 1: Add commented example to scaffold**

In the scaffold config.yaml template (line ~202), update the constraints comment block:

```yaml
# constraints:
#   timeout_seconds: 600  # kill experiment after this
#   max_retries: 3        # max consecutive failures before stop
#   max_iterations: null   # max iterations to run (null = unlimited)
```

**Step 2: Verify help text**

Run: `uv run crucible run --help`
Expected: `--max-iterations` appears in help output with description

**Step 3: Commit**

```bash
git add src/crucible/cli.py
git commit -m "docs: add max_iterations to scaffold template"
```
