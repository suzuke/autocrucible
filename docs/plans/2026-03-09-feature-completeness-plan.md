# Feature Completeness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 6 missing features to crucible: resume, structured logging, validate command, experiment comparison, JSON output, and customizable system prompt.

**Architecture:** Each feature is implemented as an independent task with its own tests. Logging is done first since other features depend on it. Features build on existing module boundaries — no new abstractions needed.

**Tech Stack:** Python 3.10+, Click CLI, PyYAML, logging module, pytest

---

### Task 1: Structured Logging — Replace print() with logging

**Files:**
- Modify: `src/crucible/cli.py:59-61` (add --verbose flag to main group)
- Modify: `src/crucible/orchestrator.py:160-167` (print → logging)
- Modify: `src/crucible/agents/claude_code.py:78-80,98-100` (print → logging)
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_verbose_flag(tmp_path):
    """--verbose flag is accepted and sets debug logging."""
    setup_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["--verbose", "status", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_verbose_flag -v`
Expected: FAIL — main group doesn't accept --verbose

**Step 3: Add --verbose flag and configure logging**

In `src/crucible/cli.py`, replace the `main` group:

```python
import logging

@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def main(verbose: bool) -> None:
    """crucible — automated experiment loop."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
```

**Step 4: Replace all print() calls with logging**

In `src/crucible/orchestrator.py`:
- Line 1: add `import logging` after existing imports
- Line 2: add `logger = logging.getLogger(__name__)`
- Line 160: `print(f"[iter ...")` → `logger.info(f"[iter {iteration}] {status} | best {self.config.metric.name}: {best_str}")`
- Line 164: `print(f"[iter ...")` → `logger.warning(f"[iter {iteration}] {max_retries} consecutive failures, stopping.")`
- Line 167: `print(f"\nStopped ...")` → `logger.info(f"Stopped after {iteration} iterations.")`

In `src/crucible/agents/claude_code.py`:
- Add `import logging` and `logger = logging.getLogger(__name__)`
- Line 79: `sys.stdout.write(f"  {line}\n")` → `logger.debug(f"  {line}")`
- Line 80: remove `sys.stdout.flush()`
- Line 98: `print("  [agent] no files changed")` → `logger.info("[agent] no files changed")`
- Line 100: `print(f"  [agent] modified: ...")` → `logger.info(f"[agent] modified: {[str(f) for f in all_files]}")`

**Step 5: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/crucible/cli.py src/crucible/orchestrator.py src/crucible/agents/claude_code.py tests/test_cli.py
git commit -m "feat: replace print() with structured logging, add --verbose flag"
```

---

### Task 2: Resume — auto-continue on existing branch

**Files:**
- Modify: `src/crucible/cli.py:176-205` (run command)
- Modify: `src/crucible/orchestrator.py:56-68` (add resume method)
- Modify: `src/crucible/git_manager.py` (add branch_exists + checkout_branch)
- Test: `tests/test_orchestrator.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_git_manager.py`

**Step 1: Write the failing test for GitManager**

Add to `tests/test_git_manager.py`:

```python
def test_branch_exists(tmp_path):
    setup_repo(tmp_path)
    gm = GitManager(workspace=tmp_path)
    gm.create_branch("run1")
    assert gm.branch_exists("run1") is True
    assert gm.branch_exists("nonexistent") is False


def test_checkout_branch(tmp_path):
    setup_repo(tmp_path)
    gm = GitManager(workspace=tmp_path)
    gm.create_branch("run1")
    # Go back to main
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    gm.checkout_branch("run1")
    # Verify we're on the right branch
    result = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path, capture_output=True, text=True)
    assert result.stdout.strip() == "crucible/run1"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_git_manager.py::test_branch_exists tests/test_git_manager.py::test_checkout_branch -v`
Expected: FAIL — methods don't exist

**Step 3: Implement GitManager methods**

Add to `src/crucible/git_manager.py`:

```python
def branch_exists(self, tag: str) -> bool:
    """Check if the experiment branch already exists."""
    branch_name = f"{self.branch_prefix}/{tag}"
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=self.workspace, capture_output=True, text=True,
    )
    return bool(result.stdout.strip())

def checkout_branch(self, tag: str) -> None:
    """Checkout an existing experiment branch."""
    branch_name = f"{self.branch_prefix}/{tag}"
    self._run("checkout", branch_name)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_git_manager.py::test_branch_exists tests/test_git_manager.py::test_checkout_branch -v`
Expected: PASS

**Step 5: Write the failing test for Orchestrator resume**

Add to `tests/test_orchestrator.py`:

```python
def test_resume_existing_branch(tmp_path):
    """Resume loads existing results and continues on the branch."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()

    # First: init normally
    orch1 = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch1.init()

    # Simulate a previous result
    orch1.results.log(commit="abc1234", metric_value=0.5, status="keep", description="first")

    # Go back to main to simulate restart
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    # Resume: should detect existing branch and continue
    orch2 = Orchestrator(cfg, tmp_path, tag="test", agent=mock_agent)
    orch2.resume()

    # Verify we're on the right branch
    result = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path, capture_output=True, text=True)
    assert result.stdout.strip() == "crucible/test"

    # Verify existing results are readable
    records = orch2.results.read_all()
    assert len(records) == 1
    assert records[0].metric_value == 0.5
```

**Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py::test_resume_existing_branch -v`
Expected: FAIL — resume() method doesn't exist

**Step 7: Implement Orchestrator.resume()**

Add to `src/crucible/orchestrator.py` after the `init` method:

```python
def resume(self) -> None:
    """Resume an existing experiment branch."""
    self.git.checkout_branch(self.tag)
    existing = self.results.read_all()
    # Restore fail sequence counter from existing crash/discard records
    self._fail_seq = sum(1 for r in existing if r.status in ("crash", "discard"))
```

**Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator.py::test_resume_existing_branch -v`
Expected: PASS

**Step 9: Write the failing test for CLI run resume**

Add to `tests/test_cli.py`:

```python
def test_run_resumes_existing_branch(tmp_path):
    """crucible run on existing branch resumes instead of failing."""
    setup_project(tmp_path)
    runner = CliRunner()
    # Init first
    runner.invoke(main, ["init", "--tag", "test1", "--project-dir", str(tmp_path)])
    # Go back to main
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    # Run should detect existing branch — will fail immediately due to no agent
    # but should NOT fail with "No results.tsv found"
    result = runner.invoke(main, ["run", "--tag", "test1", "--project-dir", str(tmp_path)])
    assert "No results.tsv found" not in (result.output or "")
```

**Step 10: Update CLI run command**

Modify `src/crucible/cli.py` run command (lines 176-205):

```python
@main.command()
@click.option("--tag", required=True, help="Experiment tag / branch suffix.")
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--model", default=None, help="Claude model to use (e.g. sonnet, opus).")
@click.option("--timeout", default=600, type=int, help="Agent timeout per iteration (seconds).")
def run(tag: str, project_dir: str, model: str | None, timeout: int) -> None:
    """Run the experiment loop until interrupted."""
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    from crucible.agents.claude_code import ClaudeCodeAgent
    from crucible.orchestrator import Orchestrator

    agent = ClaudeCodeAgent(timeout=timeout, model=model)
    orch = Orchestrator(config=config, workspace=project, tag=tag, agent=agent)

    # Resume if branch exists, otherwise require init
    if orch.git.branch_exists(tag):
        orch.resume()
        existing = orch.results.read_all()
        click.echo(f"Resuming experiment '{tag}' ({len(existing)} previous iterations)")
    else:
        results_path = project / "results.tsv"
        if not results_path.exists():
            raise click.ClickException(
                f"No results.tsv found. Run 'crucible init --tag {tag}' first."
            )

    click.echo("Press Ctrl+C to stop gracefully.")
    orch.run_loop()
    click.echo("Stopped.")
```

**Step 11: Run all tests**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 12: Commit**

```bash
git add src/crucible/cli.py src/crucible/orchestrator.py src/crucible/git_manager.py tests/test_orchestrator.py tests/test_cli.py tests/test_git_manager.py
git commit -m "feat: auto-resume experiment on existing branch"
```

---

### Task 3: JSON Output — --json flag for status and history

**Files:**
- Modify: `src/crucible/cli.py:208-251` (status + history commands)
- Test: `tests/test_cli.py`

**Step 1: Write failing tests**

Add to `tests/test_cli.py`:

```python
import json


def test_status_json_output(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "json1", "--project-dir", str(tmp_path)])
    result = runner.invoke(main, ["status", "--json", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "total" in data
    assert "kept" in data
    assert "best" in data


def test_history_json_output(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "json2", "--project-dir", str(tmp_path)])
    result = runner.invoke(main, ["history", "--json", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::test_status_json_output tests/test_cli.py::test_history_json_output -v`
Expected: FAIL — --json flag not recognized

**Step 3: Add --json to status command**

Modify `src/crucible/cli.py` status command:

```python
import json as json_module

@main.command()
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def status(project_dir: str, as_json: bool) -> None:
    """Show summary of experiment results."""
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    results = ResultsLog(project / "results.tsv")
    if not results.path.exists():
        raise click.ClickException("No results.tsv found. Run 'init' first.")

    summary = results.summary()
    best = results.best(config.metric.direction)

    if as_json:
        data = {
            "experiment": config.name,
            **summary,
            "best": {
                "metric": best.metric_value,
                "commit": best.commit,
                "description": best.description,
            } if best else None,
        }
        click.echo(json_module.dumps(data))
        return

    click.echo(f"Experiment: {config.name}")
    click.echo(f"Total: {summary['total']}  Kept: {summary['kept']}  "
               f"Discarded: {summary['discarded']}  Crashed: {summary['crashed']}")
    if best is not None:
        click.echo(f"Best {config.metric.name}: {best.metric_value} (commit {best.commit})")
```

**Step 4: Add --json to history command**

```python
@main.command()
@click.option("--last", default=10, help="Number of recent results to show.")
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def history(last: int, project_dir: str, as_json: bool) -> None:
    """Show recent experiment results."""
    project = Path(project_dir).resolve()
    results = ResultsLog(project / "results.tsv")
    if not results.path.exists():
        raise click.ClickException("No results.tsv found. Run 'init' first.")

    records = results.read_last(last)

    if as_json:
        data = [
            {"commit": r.commit, "metric": r.metric_value, "status": r.status, "description": r.description}
            for r in records
        ]
        click.echo(json_module.dumps(data))
        return

    if not records:
        click.echo("No results yet.")
        return

    click.echo(f"{'Commit':<10} {'Metric':>10} {'Status':<10} Description")
    click.echo("-" * 60)
    for r in records:
        click.echo(f"{r.commit:<10} {r.metric_value:>10.4f} {r.status:<10} {r.description}")
```

**Step 5: Run tests**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/crucible/cli.py tests/test_cli.py
git commit -m "feat: add --json flag to status and history commands"
```

---

### Task 4: Validate Command

**Files:**
- Create: `src/crucible/validator.py`
- Modify: `src/crucible/cli.py` (add validate command)
- Test: `tests/test_validator.py`

**Step 1: Write the failing test**

Create `tests/test_validator.py`:

```python
import subprocess
from pathlib import Path

import pytest

from crucible.config import load_config
from crucible.validator import validate_project, CheckResult


VALID_CONFIG = """\
name: "test"
files:
  editable: ["solution.py"]
commands:
  run: "python3 solution.py > run.log 2>&1"
  eval: "grep '^metric:' run.log"
metric:
  name: "metric"
  direction: "minimize"
"""


def setup_valid_project(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(VALID_CONFIG)
    (cfg_dir / "program.md").write_text("Optimize the metric.")
    (tmp_path / "solution.py").write_text("print('metric: 0.5')")


def test_validate_all_pass(tmp_path):
    setup_valid_project(tmp_path)
    results = validate_project(tmp_path)
    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_validate_missing_editable_file(tmp_path):
    setup_valid_project(tmp_path)
    (tmp_path / "solution.py").unlink()
    results = validate_project(tmp_path)
    file_check = [r for r in results if "editable" in r.name.lower()]
    assert any(not r.passed for r in file_check)


def test_validate_missing_program_md(tmp_path):
    setup_valid_project(tmp_path)
    (tmp_path / ".crucible" / "program.md").unlink()
    results = validate_project(tmp_path)
    prog_check = [r for r in results if "instructions" in r.name.lower()]
    assert any(not r.passed for r in prog_check)


def test_validate_run_command_fails(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    bad_config = VALID_CONFIG.replace(
        'run: "python3 solution.py > run.log 2>&1"',
        'run: "false"'
    )
    (cfg_dir / "config.yaml").write_text(bad_config)
    (cfg_dir / "program.md").write_text("Optimize.")
    (tmp_path / "solution.py").write_text("x = 1")
    results = validate_project(tmp_path)
    run_check = [r for r in results if "run command" in r.name.lower()]
    assert any(not r.passed for r in run_check)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_validator.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Implement validator.py**

Create `src/crucible/validator.py`:

```python
"""Project validation for crucible experiments."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List

from crucible.config import ConfigError, load_config
from crucible.runner import ExperimentRunner


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str


def validate_project(project_root: Path) -> List[CheckResult]:
    """Run all validation checks and return results."""
    results: List[CheckResult] = []

    # 1. Config syntax + required fields
    try:
        config = load_config(project_root)
        results.append(CheckResult("Config", True, "config.yaml is valid"))
    except ConfigError as e:
        results.append(CheckResult("Config", False, str(e)))
        return results  # can't continue without config

    # 2. Instructions file exists
    instructions_name = config.agent.instructions or "program.md"
    crucible_path = project_root / ".crucible" / instructions_name
    root_path = project_root / instructions_name
    if crucible_path.exists() and crucible_path.read_text().strip():
        results.append(CheckResult("Instructions", True, f"{crucible_path} exists"))
    elif root_path.exists() and root_path.read_text().strip():
        results.append(CheckResult("Instructions", True, f"{root_path} exists"))
    else:
        results.append(CheckResult("Instructions", False, f"{instructions_name} not found or empty"))

    # 3. Editable/readonly files exist
    all_ok = True
    for f in config.files.editable:
        if not (project_root / f).exists():
            results.append(CheckResult("Editable files", False, f"Missing: {f}"))
            all_ok = False
    for f in config.files.readonly:
        if not (project_root / f).exists():
            results.append(CheckResult("Readonly files", False, f"Missing: {f}"))
            all_ok = False
    if all_ok:
        results.append(CheckResult("Editable files", True, "All files exist"))

    # 4. Run command executes
    runner = ExperimentRunner(workspace=project_root)
    run_result = runner.execute(config.commands.run, timeout=30)
    if run_result.exit_code == 0 and not run_result.timed_out:
        results.append(CheckResult("Run command", True, "Executed successfully"))
    elif run_result.timed_out:
        results.append(CheckResult("Run command", False, "Timed out (30s)"))
    else:
        results.append(CheckResult("Run command", False, f"Exit code {run_result.exit_code}"))

    # 5. Eval command parses metric
    metric_value = runner.parse_metric(config.commands.eval, config.metric.name)
    if metric_value is not None:
        results.append(CheckResult("Eval/metric", True, f"{config.metric.name}: {metric_value}"))
    else:
        results.append(CheckResult("Eval/metric", False, f"Could not parse '{config.metric.name}' from eval output"))

    return results
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_validator.py -v`
Expected: ALL PASS

**Step 5: Write CLI test for validate command**

Add to `tests/test_cli.py`:

```python
def test_validate_command(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(VALID_CONFIG)
    (cfg_dir / "program.md").write_text("Optimize.")
    (tmp_path / "train.py").write_text("print('loss: 0.5')")
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "PASS" in result.output
```

**Step 6: Add validate command to CLI**

Add to `src/crucible/cli.py`:

```python
@main.command()
@click.option("--project-dir", default=".", help="Project root directory.")
def validate(project_dir: str) -> None:
    """Validate project configuration and run a test execution."""
    from crucible.validator import validate_project

    project = Path(project_dir).resolve()
    results = validate_project(project)

    all_passed = True
    for r in results:
        icon = "PASS" if r.passed else "FAIL"
        click.echo(f"  [{icon}] {r.name}: {r.message}")
        if not r.passed:
            all_passed = False

    if not all_passed:
        raise click.ClickException("Validation failed.")
```

**Step 7: Run all tests**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 8: Commit**

```bash
git add src/crucible/validator.py src/crucible/cli.py tests/test_validator.py tests/test_cli.py
git commit -m "feat: add crucible validate command"
```

---

### Task 5: Experiment Comparison — crucible compare

**Files:**
- Modify: `src/crucible/results.py` (add read_from_string class method)
- Modify: `src/crucible/git_manager.py` (add show_file method)
- Modify: `src/crucible/cli.py` (add compare command)
- Test: `tests/test_results.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing test for ResultsLog.read_from_string**

Add to `tests/test_results.py`:

```python
def test_read_from_string(tmp_path):
    tsv_content = "commit\tmetric_value\tstatus\tdescription\nabc1234\t0.5\tkeep\tfirst\ndef5678\t0.3\tdiscard\tsecond\n"
    records = ResultsLog.read_from_string(tsv_content)
    assert len(records) == 2
    assert records[0].metric_value == 0.5
    assert records[1].status == "discard"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_results.py::test_read_from_string -v`
Expected: FAIL — method doesn't exist

**Step 3: Implement read_from_string**

Add to `src/crucible/results.py` class ResultsLog as a `@staticmethod`:

```python
@staticmethod
def read_from_string(content: str) -> list[ExperimentRecord]:
    """Parse records from TSV string content (e.g., from git show)."""
    records: list[ExperimentRecord] = []
    lines = content.splitlines()
    for line in lines[1:]:  # skip header
        if not line.strip():
            continue
        parts = line.split("\t", maxsplit=3)
        if len(parts) < 4:
            continue
        records.append(
            ExperimentRecord(
                commit=parts[0],
                metric_value=float(parts[1]),
                status=parts[2],
                description=parts[3],
            )
        )
    return records
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_results.py::test_read_from_string -v`
Expected: PASS

**Step 5: Write the failing test for GitManager.show_file**

Add to `tests/test_git_manager.py`:

```python
def test_show_file(tmp_path):
    setup_repo(tmp_path)
    gm = GitManager(workspace=tmp_path)
    gm.create_branch("run1")
    (tmp_path / "data.txt").write_text("hello world")
    gm.commit("add data")
    content = gm.show_file("run1", "data.txt")
    assert content == "hello world"
```

**Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_git_manager.py::test_show_file -v`
Expected: FAIL

**Step 7: Implement show_file**

Add to `src/crucible/git_manager.py`:

```python
def show_file(self, tag: str, file_path: str) -> str:
    """Read a file's content from a specific experiment branch."""
    branch_name = f"{self.branch_prefix}/{tag}"
    return self._run("show", f"{branch_name}:{file_path}")
```

**Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_git_manager.py::test_show_file -v`
Expected: PASS

**Step 9: Write CLI compare test**

Add to `tests/test_cli.py`:

```python
def test_compare_command(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    # Create two branches with results
    runner.invoke(main, ["init", "--tag", "a", "--project-dir", str(tmp_path)])
    # Write a result into results.tsv and commit it to the branch
    results_path = tmp_path / "results.tsv"
    with results_path.open("a") as f:
        f.write("abc1234\t0.5\tkeep\tfirst improvement\n")
    subprocess.run(["git", "add", "results.tsv"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "result"], cwd=tmp_path, check=True, capture_output=True)
    # Go back to main and create another branch
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    runner.invoke(main, ["init", "--tag", "b", "--project-dir", str(tmp_path)])
    with results_path.open("a") as f:
        f.write("def5678\t0.3\tkeep\tsecond improvement\n")
    subprocess.run(["git", "add", "results.tsv"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "result"], cwd=tmp_path, check=True, capture_output=True)

    result = runner.invoke(main, ["compare", "a", "b", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "a" in result.output
    assert "b" in result.output


def test_compare_json_output(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "x", "--project-dir", str(tmp_path)])
    subprocess.run(["git", "add", "results.tsv"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "result"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    runner.invoke(main, ["init", "--tag", "y", "--project-dir", str(tmp_path)])
    subprocess.run(["git", "add", "results.tsv"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "result"], cwd=tmp_path, check=True, capture_output=True)

    result = runner.invoke(main, ["compare", "x", "y", "--json", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "x" in data
    assert "y" in data
```

**Step 10: Implement compare command**

Add to `src/crucible/cli.py`:

```python
@main.command()
@click.argument("tags", nargs=2)
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def compare(tags: tuple[str, str], project_dir: str, as_json: bool) -> None:
    """Compare two experiment runs side by side."""
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    from crucible.git_manager import GitManager

    git = GitManager(workspace=project, branch_prefix=config.git.branch_prefix)
    comparison = {}

    for tag in tags:
        try:
            content = git.show_file(tag, "results.tsv")
        except subprocess.CalledProcessError:
            raise click.ClickException(f"Cannot read results.tsv from branch {config.git.branch_prefix}/{tag}")
        records = ResultsLog.read_from_string(content)
        kept = [r for r in records if r.status == "keep"]
        best = None
        if kept:
            if config.metric.direction == "minimize":
                best = min(kept, key=lambda r: r.metric_value)
            else:
                best = max(kept, key=lambda r: r.metric_value)
        comparison[tag] = {
            "iterations": len(records),
            "kept": len(kept),
            "discarded": sum(1 for r in records if r.status == "discard"),
            "crashed": sum(1 for r in records if r.status == "crash"),
            "best_metric": best.metric_value if best else None,
            "best_commit": best.commit if best else None,
        }

    if as_json:
        click.echo(json_module.dumps(comparison))
        return

    # Table output
    tag_a, tag_b = tags
    col_w = max(len(tag_a), len(tag_b), 12)
    click.echo(f"{'':>16} {tag_a:>{col_w}} {tag_b:>{col_w}}")
    for key in ("iterations", "kept", "discarded", "crashed", "best_metric", "best_commit"):
        va = comparison[tag_a].get(key, "N/A")
        vb = comparison[tag_b].get(key, "N/A")
        label = key.replace("_", " ").title()
        click.echo(f"{label:>16} {str(va):>{col_w}} {str(vb):>{col_w}}")
```

**Step 11: Run all tests**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 12: Commit**

```bash
git add src/crucible/cli.py src/crucible/results.py src/crucible/git_manager.py tests/test_cli.py tests/test_results.py tests/test_git_manager.py
git commit -m "feat: add crucible compare command with --json support"
```

---

### Task 6: Customizable System Prompt

**Files:**
- Modify: `src/crucible/config.py:24-28` (add system_prompt field to AgentConfig)
- Modify: `src/crucible/agents/claude_code.py:23-28,31-34,60-61` (accept custom system prompt)
- Modify: `src/crucible/orchestrator.py:162` (pass config to agent)
- Test: `tests/test_config.py`
- Test: `tests/test_agents.py`

**Step 1: Write the failing test for config parsing**

Add to `tests/test_config.py`:

```python
def test_system_prompt_config(tmp_path):
    """agent.system_prompt field is parsed from config."""
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
    from crucible.config import load_config
    config = load_config(tmp_path)
    assert config.agent.system_prompt == "custom_system.md"


def test_system_prompt_default_none(tmp_path):
    """agent.system_prompt defaults to None when not specified."""
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
    from crucible.config import load_config
    config = load_config(tmp_path)
    assert config.agent.system_prompt is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_system_prompt_config -v`
Expected: FAIL — AgentConfig has no system_prompt field

**Step 3: Add system_prompt to AgentConfig**

In `src/crucible/config.py`, modify AgentConfig:

```python
@dataclass
class AgentConfig:
    type: str = "claude-code"
    instructions: Optional[str] = None
    system_prompt: Optional[str] = None
    context_window: ContextWindowConfig = field(default_factory=ContextWindowConfig)
```

And in `_build_agent`:

```python
def _build_agent(data: dict) -> AgentConfig:
    if not data:
        return AgentConfig()
    return AgentConfig(
        type=data.get("type", "claude-code"),
        instructions=data.get("instructions"),
        system_prompt=data.get("system_prompt"),
        context_window=_build_context_window(data.get("context_window", {})),
    )
```

**Step 4: Run config tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: ALL PASS

**Step 5: Write the failing test for agent using custom prompt**

Add to `tests/test_agents.py`:

```python
def test_custom_system_prompt(tmp_path):
    """ClaudeCodeAgent uses custom system prompt when provided."""
    from crucible.agents.claude_code import ClaudeCodeAgent, SYSTEM_PROMPT
    agent = ClaudeCodeAgent()
    # Default prompt
    assert agent.get_system_prompt(tmp_path) == SYSTEM_PROMPT
    # Custom prompt
    crucible_dir = tmp_path / ".crucible"
    crucible_dir.mkdir()
    (crucible_dir / "my_prompt.md").write_text("You are a custom agent.")
    agent = ClaudeCodeAgent(system_prompt_file="my_prompt.md")
    assert agent.get_system_prompt(tmp_path) == "You are a custom agent."
```

**Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_agents.py::test_custom_system_prompt -v`
Expected: FAIL

**Step 7: Implement custom system prompt in ClaudeCodeAgent**

Modify `src/crucible/agents/claude_code.py`:

```python
class ClaudeCodeAgent(AgentInterface):
    def __init__(
        self,
        timeout: int = DEFAULT_AGENT_TIMEOUT,
        model: str | None = None,
        system_prompt_file: str | None = None,
    ):
        self.timeout = timeout
        self.model = model
        self.system_prompt_file = system_prompt_file

    def get_system_prompt(self, workspace: Path) -> str:
        """Return system prompt: custom file content or default."""
        if self.system_prompt_file:
            prompt_path = workspace / ".crucible" / self.system_prompt_file
            if prompt_path.exists():
                return prompt_path.read_text().strip()
        return SYSTEM_PROMPT
```

Then update `_run_query` to use it:

```python
async def _run_query(self, prompt: str, workspace: Path) -> AgentResult:
    options = ClaudeAgentOptions(
        system_prompt=self.get_system_prompt(workspace),
        ...
    )
```

**Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_agents.py::test_custom_system_prompt -v`
Expected: PASS

**Step 9: Wire up in CLI**

Modify `src/crucible/cli.py` where ClaudeCodeAgent is instantiated (in both `init` and `run` commands):

```python
agent = ClaudeCodeAgent(
    timeout=timeout,
    model=model,
    system_prompt_file=config.agent.system_prompt,
)
```

For `init` command (no timeout/model params):

```python
agent = ClaudeCodeAgent(system_prompt_file=config.agent.system_prompt)
```

**Step 10: Run all tests**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 11: Commit**

```bash
git add src/crucible/config.py src/crucible/agents/claude_code.py src/crucible/cli.py tests/test_config.py tests/test_agents.py
git commit -m "feat: support customizable system prompt via agent.system_prompt config"
```

---

### Task 7: Final Integration Verification

**Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS (original 47 + new tests)

**Step 2: Manual smoke test**

Run: `uv run crucible --help`
Expected: Shows all commands including `validate` and `compare`

Run: `uv run crucible validate --help`
Expected: Shows validate options

Run: `uv run crucible compare --help`
Expected: Shows compare usage

Run: `uv run crucible status --help`
Expected: Shows --json flag

**Step 3: Update CLAUDE.md**

Add new commands to CLAUDE.md Build & Development Commands section and update module list.

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with new commands"
```
