# Fork Baseline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow new experiment runs to fork from a previous run's best commit, inheriting its score as the initial baseline threshold.

**Architecture:** Add `seed_baseline()` to ResultsLog, `create_branch_from()` to GitManager, wire fork logic through Orchestrator.init(), and add interactive menu to CLI's `run` command. Baseline records use `status=baseline` in TSV, treated as `keep` for comparison purposes.

**Tech Stack:** Python, Click (CLI), git subprocess, pytest

---

### Task 1: ResultsLog — seed_baseline() and baseline-aware best()

**Files:**
- Modify: `src/crucible/results.py:83-105` (best() and is_improvement())
- Modify: `src/crucible/results.py:50` (ResultsLog class — add seed_baseline method)
- Test: `tests/test_results.py`

**Step 1: Write the failing tests**

Add to `tests/test_results.py`:

```python
def test_seed_baseline(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    records = log.read_all()
    assert len(records) == 1
    assert records[0].status == "baseline"
    assert records[0].metric_value == 600.0
    assert records[0].commit == "abc1234"
    assert "run1" in records[0].description


def test_best_includes_baseline_maximize(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    best = log.best("maximize")
    assert best is not None
    assert best.metric_value == 600.0
    assert best.status == "baseline"


def test_best_includes_baseline_minimize(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(0.3, "abc1234", "run1")
    best = log.best("minimize")
    assert best is not None
    assert best.metric_value == 0.3


def test_is_improvement_with_baseline_maximize(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    assert log.is_improvement(601.0, "maximize") is True
    assert log.is_improvement(600.0, "maximize") is False
    assert log.is_improvement(500.0, "maximize") is False


def test_is_improvement_with_baseline_minimize(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(0.5, "abc1234", "run1")
    assert log.is_improvement(0.4, "minimize") is True
    assert log.is_improvement(0.5, "minimize") is False
    assert log.is_improvement(0.6, "minimize") is False


def test_best_prefers_keep_over_baseline_when_better(tmp_path):
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    log.log("def5678", 700.0, "keep", "improvement")
    best = log.best("maximize")
    assert best.metric_value == 700.0
    assert best.status == "keep"


def test_summary_excludes_baseline(tmp_path):
    """Baseline records should not be counted in summary totals."""
    tsv = tmp_path / "results.tsv"
    log = ResultsLog(tsv)
    log.init()
    log.seed_baseline(600.0, "abc1234", "run1")
    log.log("def5678", 700.0, "keep", "improvement")
    s = log.summary()
    assert s["total"] == 1  # baseline not counted
    assert s["kept"] == 1
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_results.py -v -k "baseline"`
Expected: FAIL — `seed_baseline` not defined, `best()` doesn't include baseline

**Step 3: Write minimal implementation**

In `src/crucible/results.py`, add `seed_baseline()` method to `ResultsLog` (after `init()`):

```python
def seed_baseline(self, value: float, commit: str, source_tag: str) -> None:
    """Write a baseline record from a previous run's best result."""
    self.log(
        commit=commit,
        metric_value=value,
        status="baseline",
        description=f"Forked from {source_tag} best",
    )
```

Modify `best()` to include baseline records:

```python
def best(self, direction: str) -> Optional[ExperimentRecord]:
    """Return the best record among those with status 'keep' or 'baseline'."""
    candidates = [r for r in self.read_all() if r.status in ("keep", "baseline")]
    if not candidates:
        return None
    if direction == "minimize":
        return min(candidates, key=lambda r: r.metric_value)
    return max(candidates, key=lambda r: r.metric_value)
```

Modify `summary()` to exclude baseline:

```python
def summary(self) -> dict[str, int]:
    """Return counts by status category (excludes baseline)."""
    records = [r for r in self.read_all() if r.status != "baseline"]
    return {
        "total": len(records),
        "kept": sum(1 for r in records if r.status == "keep"),
        "discarded": sum(1 for r in records if r.status == "discard"),
        "crashed": sum(1 for r in records if r.status == "crash"),
    }
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_results.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/crucible/results.py tests/test_results.py
git commit -m "feat: add baseline support to ResultsLog"
```

---

### Task 2: GitManager — create_branch_from()

**Files:**
- Modify: `src/crucible/git_manager.py:34` (add new method after create_branch)
- Test: `tests/test_git_manager.py`

**Step 1: Write the failing test**

Add to `tests/test_git_manager.py`:

```python
def test_create_branch_from_commit(git_repo):
    """Create a branch starting from a specific commit, not HEAD."""
    gm = GitManager(git_repo, branch_prefix="crucible", tag_failed=True)
    # Make a second commit
    (git_repo / "file.txt").write_text("second")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "second"], cwd=git_repo, check=True, capture_output=True)
    # Get the first commit hash
    first_commit = subprocess.run(
        ["git", "rev-parse", "HEAD~1"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    # Create branch from first commit
    gm.create_branch_from("forked", first_commit)
    # Should be on the new branch
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=git_repo, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "crucible/forked"
    # File content should match first commit (not second)
    assert (git_repo / "file.txt").read_text() == "initial"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_git_manager.py::test_create_branch_from_commit -v`
Expected: FAIL — `create_branch_from` not defined

**Step 3: Write minimal implementation**

In `src/crucible/git_manager.py`, add after `create_branch()`:

```python
def create_branch_from(self, tag: str, commit: str) -> None:
    """Create and checkout a new branch starting from a specific commit."""
    branch_name = f"{self.branch_prefix}/{tag}"
    self._run("checkout", "-b", branch_name, commit)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_git_manager.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/crucible/git_manager.py tests/test_git_manager.py
git commit -m "feat: add create_branch_from() to GitManager"
```

---

### Task 3: Orchestrator — fork_from parameter in init()

**Files:**
- Modify: `src/crucible/orchestrator.py:59-71` (init method)
- Test: `tests/test_orchestrator.py`

**Step 1: Write the failing tests**

Add to `tests/test_orchestrator.py`:

```python
def test_init_with_fork_from(tmp_path):
    """init() with fork_from creates branch from specified commit and seeds baseline."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()

    # Get initial commit hash
    initial_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Make a second commit (simulating work done in run1)
    (tmp_path / "train.py").write_text("x = 2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "run1 best"], cwd=tmp_path, check=True, capture_output=True)
    best_commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    best_commit_full = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()

    orch = Orchestrator(cfg, tmp_path, tag="run2", agent=mock_agent)
    orch.init(fork_from=(best_commit_full, 600.0, "run1"))

    # Should be on run2 branch
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path, capture_output=True, text=True,
    ).stdout.strip()
    assert branch == "crucible/run2"

    # Should have baseline record
    records = orch.results.read_all()
    assert len(records) == 1
    assert records[0].status == "baseline"
    assert records[0].metric_value == 600.0

    # Code state should match the forked commit
    assert (tmp_path / "train.py").read_text() == "x = 2"


def test_init_without_fork_from_unchanged(tmp_path):
    """init() without fork_from works exactly as before."""
    setup_repo(tmp_path)
    cfg = make_config()
    mock_agent = MagicMock()

    orch = Orchestrator(cfg, tmp_path, tag="run1", agent=mock_agent)
    orch.init()

    records = orch.results.read_all()
    assert len(records) == 0  # No baseline

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path, capture_output=True, text=True,
    ).stdout.strip()
    assert branch == "crucible/run1"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -v -k "fork"`
Expected: FAIL — `init()` doesn't accept `fork_from`

**Step 3: Write minimal implementation**

In `src/crucible/orchestrator.py`, modify `init()`:

```python
def init(self, fork_from: tuple[str, float, str] | None = None) -> None:
    """Create the experiment branch and initialise results-{tag}.tsv.

    Args:
        fork_from: Optional (commit, metric_value, source_tag) to fork from
                   a previous run's best result.
    """
    if fork_from is not None:
        commit, metric_value, source_tag = fork_from
        self.git.create_branch_from(self.tag, commit)
    else:
        self.git.create_branch(self.tag)
    self.results.init()
    if fork_from is not None:
        commit, metric_value, source_tag = fork_from
        self.results.seed_baseline(metric_value, commit[:7], source_tag)
    # Ensure generated files are gitignored so reset doesn't revert them
    # and agents don't trigger violations by accidentally touching them
    gitignore = self.workspace / ".gitignore"
    lines = gitignore.read_text().splitlines() if gitignore.exists() else []
    needed = [p for p in ("results-*.tsv", "run.log") if p not in lines]
    if needed:
        lines.extend(needed)
        gitignore.write_text("\n".join(lines) + "\n")
        self.git.commit("chore: gitignore generated files")
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/crucible/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add fork_from support to Orchestrator.init()"
```

---

### Task 4: ContextAssembler — baseline-aware display

**Files:**
- Modify: `src/crucible/context.py:127-162` (_section_state)
- Modify: `src/crucible/context.py:271-302` (assemble — include baseline in best calc)
- Modify: `src/crucible/context.py:164-221` (_section_history — filter baseline from history table, handle in strategy hint)
- Test: `tests/test_context.py`

**Step 1: Write the failing tests**

Add to `tests/test_context.py`:

```python
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
    assert "Baseline" in prompt or "baseline" in prompt


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
    # Baseline should not be in the history table rows
    assert "Forked from" not in prompt.split("History")[1].split("Key Lessons")[0] if "Key Lessons" in prompt else True
    # But the real experiment should be there
    assert "first real improvement" in prompt
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context.py -v -k "baseline"`
Expected: FAIL — context doesn't handle baseline status

**Step 3: Write minimal implementation**

In `src/crucible/context.py`, modify `assemble()` to include baseline in best calculation:

```python
def assemble(self, log: ResultsLog) -> str:
    """Assemble all sections into a complete prompt."""
    records = log.read_all()
    direction = self.config.metric.direction
    candidates = [r for r in records if r.status in ("keep", "baseline")]
    if candidates:
        best = min(candidates, key=lambda r: r.metric_value) if direction == "minimize" else max(candidates, key=lambda r: r.metric_value)
    else:
        best = None
    # Filter out baseline for summary and history
    real_records = [r for r in records if r.status != "baseline"]
    summary = {
        "total": len(real_records),
        "kept": sum(1 for r in real_records if r.status == "keep"),
        "discarded": sum(1 for r in real_records if r.status == "discard"),
        "crashed": sum(1 for r in real_records if r.status == "crash"),
    }

    sections = [
        self._section_instructions(),
        self._section_state(real_records, best, summary),
        self._section_history(real_records),
        self._section_errors(),
        self._section_directive(),
    ]
    prompt = "\n\n---\n\n".join(s for s in sections if s)

    # Save crash info before clearing (for requeue on skip iterations)
    self._last_crash_info = list(self._crash_info)
    # Clear transient context after assembly
    self._errors.clear()
    self._crash_info.clear()

    return PREAMBLE + "\n---\n\n" + prompt
```

Modify `_section_state()` to show baseline info when best is a baseline record:

```python
def _section_state(self, records: list, best, summary: dict) -> str:
    """Section 2: Current state — branch, best metric, summary, editable files."""
    lines = ["## Current State"]
    lines.append(f"\nBranch: {self.branch_name}")

    if best is not None:
        direction_hint = (
            "lower is better" if self.config.metric.direction == "minimize"
            else "higher is better"
        )
        if best.status == "baseline":
            lines.append(
                f"**Baseline from previous run: {best.metric_value}** "
                f"(Goal: {self.config.metric.direction} — {direction_hint}) "
                f"— you must beat this score."
            )
        else:
            lines.append(
                f"**Best {self.config.metric.name} so far: {best.metric_value}** "
                f"(Goal: {self.config.metric.direction} — {direction_hint})"
            )
    # ... rest unchanged
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_context.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/crucible/context.py tests/test_context.py
git commit -m "feat: baseline-aware context assembly"
```

---

### Task 5: CLI — interactive fork menu in `run` command

**Files:**
- Modify: `src/crucible/cli.py:350-403` (run command)
- Test: `tests/test_cli.py`

**Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_run_shows_fork_menu_when_previous_runs_exist(tmp_path):
    """When previous results exist, run should show fork menu."""
    setup_project(tmp_path)
    runner = CliRunner()
    # Create run1 with results
    runner.invoke(main, ["init", "--tag", "run1", "--project-dir", str(tmp_path)])
    with (tmp_path / results_filename("run1")).open("a") as f:
        f.write("abc1234\t0.5\tkeep\tfirst improvement\n")
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    # Run run2 — user selects "Start fresh" (option 2)
    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(
            main,
            ["run", "--tag", "run2", "--project-dir", str(tmp_path)],
            input="2\n",  # "Start fresh"
        )
    assert result.exit_code == 0, result.output
    assert "previous experiment" in result.output.lower() or "run1" in result.output


def test_run_fork_from_previous(tmp_path):
    """Selecting a previous run forks from its best commit."""
    setup_project(tmp_path)
    runner = CliRunner()
    # Create run1 with a commit and results
    runner.invoke(main, ["init", "--tag", "run1", "--project-dir", str(tmp_path)])
    (tmp_path / "train.py").write_text("x = 2")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "improvement"], cwd=tmp_path, check=True, capture_output=True)
    best_commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    with (tmp_path / results_filename("run1")).open("a") as f:
        f.write(f"{best_commit}\t0.5\tkeep\timprovement\n")
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    # Run run2 — user selects run1 (option 1)
    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(
            main,
            ["run", "--tag", "run2", "--project-dir", str(tmp_path)],
            input="1\n",
        )
    assert result.exit_code == 0, result.output
    assert "fork" in result.output.lower() or "Forking" in result.output
    # Verify baseline was seeded
    results_run2 = tmp_path / results_filename("run2")
    assert results_run2.exists()
    content = results_run2.read_text()
    assert "baseline" in content


def test_run_no_interactive_skips_menu(tmp_path):
    """--no-interactive skips the fork menu."""
    setup_project(tmp_path)
    runner = CliRunner()
    # Create run1 with results
    runner.invoke(main, ["init", "--tag", "run1", "--project-dir", str(tmp_path)])
    with (tmp_path / results_filename("run1")).open("a") as f:
        f.write("abc1234\t0.5\tkeep\timprovement\n")
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(
            main,
            ["run", "--tag", "run2", "--no-interactive", "--project-dir", str(tmp_path)],
        )
    assert result.exit_code == 0, result.output
    # Should not prompt — just start fresh
    assert "Initialised" in result.output


def test_run_no_menu_when_no_previous_runs(tmp_path):
    """No menu when there are no previous results files."""
    setup_project(tmp_path)
    runner = CliRunner()
    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(
            main,
            ["run", "--tag", "run1", "--project-dir", str(tmp_path)],
        )
    assert result.exit_code == 0, result.output
    assert "Initialised" in result.output
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v -k "fork or no_interactive or no_menu"`
Expected: FAIL — `--no-interactive` not recognized, no menu logic

**Step 3: Write minimal implementation**

Add helper function and modify `run` command in `src/crucible/cli.py`:

```python
def _scan_previous_runs(project: Path, current_tag: str, direction: str) -> list[dict]:
    """Scan for previous experiment results and return their best scores."""
    import glob
    previous = []
    for tsv_path in sorted(project.glob("results-*.tsv")):
        tag = tsv_path.stem.removeprefix("results-")
        if tag == current_tag:
            continue
        log = ResultsLog(tsv_path)
        records = log.read_all()
        kept = [r for r in records if r.status == "keep"]
        if not kept:
            continue
        if direction == "minimize":
            best = min(kept, key=lambda r: r.metric_value)
        else:
            best = max(kept, key=lambda r: r.metric_value)
        previous.append({
            "tag": tag,
            "best_metric": best.metric_value,
            "best_commit": best.commit,
            "iterations": len([r for r in records if r.status != "baseline"]),
            "kept": len(kept),
        })
    # Sort by best metric (best first)
    if direction == "minimize":
        previous.sort(key=lambda x: x["best_metric"])
    else:
        previous.sort(key=lambda x: x["best_metric"], reverse=True)
    return previous
```

Modify the `run` command to add `--no-interactive` and fork menu:

```python
@main.command()
@click.option("--tag", required=True, help="Experiment tag / branch suffix.")
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--model", default=None, help="Claude model to use (e.g. sonnet, opus).")
@click.option("--timeout", default=600, type=int, help="Agent timeout per iteration (seconds).")
@click.option("--no-interactive", is_flag=True, default=False, help="Skip interactive prompts (start fresh).")
@_verbose_option
def run(tag: str, project_dir: str, model: str | None, timeout: int, no_interactive: bool) -> None:
    """Run the experiment loop until interrupted."""
    # ... existing config loading and agent setup ...

    # Resume if branch exists, otherwise auto-init
    if orch.git.branch_exists(tag):
        orch.resume()
        # ... existing resume logic ...
    else:
        # ... existing git init logic ...

        # Check for previous runs to fork from
        fork_from = None
        if not no_interactive:
            previous = _scan_previous_runs(project, tag, config.metric.direction)
            if previous:
                click.echo("\nFound previous experiments:")
                for i, prev in enumerate(previous, 1):
                    click.echo(
                        f"  {i}) {prev['tag']}  — best: {prev['best_metric']} "
                        f"(commit {prev['best_commit']}, {prev['iterations']} iters, "
                        f"{prev['kept']} kept)"
                    )
                click.echo(f"  {len(previous) + 1}) Start fresh")
                choice = click.prompt(
                    "Fork from",
                    type=int,
                    default=len(previous) + 1,
                )
                if 1 <= choice <= len(previous):
                    selected = previous[choice - 1]
                    fork_from = (
                        selected["best_commit"],
                        selected["best_metric"],
                        selected["tag"],
                    )
                    click.echo(
                        f"Forking from {selected['tag']} best "
                        f"({selected['best_metric']} @ {selected['best_commit']})..."
                    )

        orch.init(fork_from=fork_from)
        # ... existing setup command logic ...
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/crucible/cli.py tests/test_cli.py
git commit -m "feat: interactive fork menu in crucible run"
```

---

### Task 6: Full integration test

**Files:**
- Test: `tests/test_cli.py` (or `tests/test_integration.py`)

**Step 1: Write end-to-end test**

Add to `tests/test_cli.py`:

```python
def test_fork_baseline_full_flow(tmp_path):
    """End-to-end: run1 produces results, run2 forks from run1's best."""
    setup_project(tmp_path)
    runner = CliRunner()

    # === Run1: init and add results ===
    runner.invoke(main, ["init", "--tag", "run1", "--project-dir", str(tmp_path)])
    # Simulate agent making an improvement
    (tmp_path / "train.py").write_text("x = optimized")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "optimize"], cwd=tmp_path, check=True, capture_output=True)
    best_commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    with (tmp_path / results_filename("run1")).open("a") as f:
        f.write(f"{best_commit}\t0.3\tkeep\toptimized x\n")

    # Go back to main
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)

    # === Run2: fork from run1 ===
    with patch("crucible.orchestrator.Orchestrator.run_loop"):
        result = runner.invoke(
            main,
            ["run", "--tag", "run2", "--project-dir", str(tmp_path)],
            input="1\n",  # Select run1
        )
    assert result.exit_code == 0, result.output

    # Verify: run2 branch has run1's code
    assert (tmp_path / "train.py").read_text() == "x = optimized"

    # Verify: run2 results have baseline
    from crucible.results import ResultsLog
    log2 = ResultsLog(tmp_path / results_filename("run2"))
    records = log2.read_all()
    assert len(records) == 1
    assert records[0].status == "baseline"
    assert records[0].metric_value == 0.3

    # Verify: is_improvement uses baseline as threshold
    assert log2.is_improvement(0.29, "minimize") is True
    assert log2.is_improvement(0.31, "minimize") is False
```

**Step 2: Run test**

Run: `uv run pytest tests/test_cli.py::test_fork_baseline_full_flow -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: add fork baseline end-to-end test"
```

---

### Task 7: Run full test suite

**Step 1: Run all tests**

Run: `uv run pytest -v`
Expected: ALL PASS — no regressions

**Step 2: Verify existing tests still pass**

Pay special attention to:
- `test_results.py` — existing best/is_improvement tests unaffected
- `test_orchestrator.py` — existing init/resume tests unaffected
- `test_context.py` — existing assemble tests unaffected (no baseline records in old tests)
- `test_cli.py` — existing run/init/compare tests unaffected

**Step 3: Final commit if any fixups needed**

```bash
git add -A
git commit -m "fix: address test regressions from fork baseline feature"
```
