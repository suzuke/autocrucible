# Persistent Artifacts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `files.artifacts` config option so experiment projects can persist model weights and training data across iterations.

**Architecture:** Add `artifacts` field to `FilesConfig`, auto-create directories and add to `.gitignore` in orchestrator init, inform agent via context prompt, and mount as rw volumes in Docker sandbox. Git revert already respects `.gitignore` — no git_manager changes needed.

**Tech Stack:** Python dataclasses, subprocess (git), Docker volume mounts

---

### Task 1: Config — add `artifacts` field to `FilesConfig`

**Files:**
- Modify: `src/crucible/config.py:34-37` (FilesConfig dataclass)
- Modify: `src/crucible/config.py:206-210` (load_config files parsing)
- Test: `tests/test_config.py`

**Step 1: Write failing test**

```python
def test_load_config_with_artifacts(tmp_path):
    """Config loads files.artifacts as a list of paths."""
    crucible_dir = tmp_path / ".crucible"
    crucible_dir.mkdir()
    (crucible_dir / "config.yaml").write_text("""
name: test
files:
  editable: ["main.py"]
  artifacts: ["artifacts/", "checkpoints/"]
commands:
  run: "echo ok"
  eval: "echo ok"
metric:
  name: score
  direction: maximize
""")
    (tmp_path / "main.py").touch()
    from crucible.config import load_config
    config = load_config(tmp_path)
    assert config.files.artifacts == ["artifacts/", "checkpoints/"]


def test_load_config_without_artifacts(tmp_path):
    """Config defaults artifacts to empty list when not specified."""
    crucible_dir = tmp_path / ".crucible"
    crucible_dir.mkdir()
    (crucible_dir / "config.yaml").write_text("""
name: test
files:
  editable: ["main.py"]
commands:
  run: "echo ok"
  eval: "echo ok"
metric:
  name: score
  direction: maximize
""")
    (tmp_path / "main.py").touch()
    from crucible.config import load_config
    config = load_config(tmp_path)
    assert config.files.artifacts == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k "artifacts" -v`
Expected: FAIL — `FilesConfig` has no `artifacts` attribute

**Step 3: Implement**

In `src/crucible/config.py`, add `artifacts` to `FilesConfig`:

```python
@dataclass
class FilesConfig:
    editable: List[str] = field(default_factory=list)
    readonly: List[str] = field(default_factory=list)
    hidden: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
```

In `load_config()`, parse the new field (around line 206-210):

```python
files=FilesConfig(
    editable=files_data.get("editable", []),
    readonly=files_data.get("readonly", []),
    hidden=files_data.get("hidden", []),
    artifacts=files_data.get("artifacts", []),
),
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k "artifacts" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/crucible/config.py tests/test_config.py
git commit -m "feat: add files.artifacts config field"
```

---

### Task 2: Orchestrator — create artifacts dirs and gitignore on init

**Files:**
- Modify: `src/crucible/orchestrator.py:84-107` (init method)
- Test: `tests/test_orchestrator.py`

**Step 1: Write failing test**

```python
def test_init_creates_artifacts_dirs_and_gitignore(tmp_path):
    """Orchestrator.init() creates artifact directories and adds them to .gitignore."""
    # Set up a minimal git repo + config with artifacts
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, capture_output=True)

    config = make_config()  # use existing test helper
    config.files.artifacts = ["artifacts/", "weights/"]

    from unittest.mock import MagicMock
    agent = MagicMock()
    agent.capabilities.return_value = {"read", "edit"}

    orch = Orchestrator(config=config, workspace=tmp_path, tag="test", agent=agent)
    orch.init()

    # Directories created
    assert (tmp_path / "artifacts").is_dir()
    assert (tmp_path / "weights").is_dir()

    # Added to .gitignore
    gitignore = (tmp_path / ".gitignore").read_text()
    assert "artifacts/" in gitignore
    assert "weights/" in gitignore
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py -k "artifacts" -v`
Expected: FAIL — artifacts dirs not created

**Step 3: Implement**

In `orchestrator.py` `init()` method, after the existing gitignore logic (around line 99-107), add artifacts setup:

```python
# Ensure generated files are gitignored ...
gitignore = self.workspace / ".gitignore"
lines = gitignore.read_text().splitlines() if gitignore.exists() else []
needed = [p for p in ("results-*.jsonl", "run.log", "logs/") if p not in lines]

# Add artifacts paths to gitignore and create directories
for artifact_path in self.config.files.artifacts:
    if artifact_path not in lines:
        needed.append(artifact_path)
    # Create the directory
    (self.workspace / artifact_path).mkdir(parents=True, exist_ok=True)

if needed:
    lines.extend(needed)
    gitignore.write_text("\n".join(lines) + "\n")
    self.git.commit("chore: gitignore generated files")
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_orchestrator.py -k "artifacts" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/crucible/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator creates artifacts dirs and gitignores them"
```

---

### Task 3: Context — inform agent about artifacts directories

**Files:**
- Modify: `src/crucible/context.py:154-210` (_section_state method)
- Test: `tests/test_context.py`

**Step 1: Write failing test**

```python
def test_section_state_shows_artifacts(tmp_path):
    """Context includes artifacts info when configured."""
    config = Config(
        name="test",
        files=FilesConfig(
            editable=["main.py"],
            artifacts=["artifacts/", "weights/"],
        ),
        metric=MetricConfig(name="score", direction="maximize"),
    )
    ctx = ContextAssembler(config=config, project_root=tmp_path, branch_name="test/tag")
    state = ctx._section_state([], None, {"total": 0, "kept": 0, "discarded": 0, "crashed": 0})
    assert "Persistent directories" in state
    assert "artifacts/" in state
    assert "weights/" in state
    assert "survive across iterations" in state


def test_section_state_no_artifacts_when_empty(tmp_path):
    """Context omits artifacts section when none configured."""
    config = Config(
        name="test",
        files=FilesConfig(editable=["main.py"]),
        metric=MetricConfig(name="score", direction="maximize"),
    )
    ctx = ContextAssembler(config=config, project_root=tmp_path, branch_name="test/tag")
    state = ctx._section_state([], None, {"total": 0, "kept": 0, "discarded": 0, "crashed": 0})
    assert "Persistent" not in state
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context.py -k "artifacts" -v`
Expected: FAIL — no artifacts text in output

**Step 3: Implement**

In `context.py` `_section_state()`, after the hidden files block (around line 194) and before the allow_install block:

```python
if self.config.files.artifacts:
    artifact_list = ", ".join(self.config.files.artifacts)
    lines.append(
        f"Persistent directories (survive across iterations, not version-controlled): {artifact_list}"
    )
    lines.append(
        "Files in these directories are NOT affected by revert. "
        "Use them to store model weights, training data, or other artifacts that should persist."
    )
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_context.py -k "artifacts" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/crucible/context.py tests/test_context.py
git commit -m "feat: context informs agent about persistent artifacts dirs"
```

---

### Task 4: Sandbox — mount artifacts as rw volumes in Docker

**Files:**
- Modify: `src/crucible/sandbox.py:30-42` (SandboxRunner __init__)
- Modify: `src/crucible/sandbox.py:69-98` (_docker_run)
- Modify: `src/crucible/orchestrator.py:54-61` (SandboxRunner creation)
- Test: `tests/test_sandbox.py`

**Step 1: Write failing test**

```python
def test_docker_run_mounts_artifacts(tmp_path):
    """Docker run command includes rw volume mounts for artifacts."""
    from crucible.config import SandboxConfig
    from crucible.sandbox import SandboxRunner

    (tmp_path / "main.py").touch()
    runner = SandboxRunner(
        config=SandboxConfig(backend="docker"),
        workspace=tmp_path,
        editable_files=["main.py"],
        artifact_dirs=["artifacts/", "weights/"],
    )

    # We need to inspect the docker command that would be built.
    # Mock subprocess.Popen to capture the command.
    import subprocess
    from unittest.mock import patch, MagicMock

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "")
    mock_proc.returncode = 0

    with patch.object(runner, "_ensure_image", return_value="test:latest"), \
         patch("crucible.sandbox.subprocess.Popen", return_value=mock_proc) as mock_popen:
        runner._docker_run("echo test", 60)
        cmd = mock_popen.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert f"{tmp_path}/artifacts:/workspace/artifacts/:rw" in cmd_str
        assert f"{tmp_path}/weights:/workspace/weights/:rw" in cmd_str
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sandbox.py -k "mounts_artifacts" -v`
Expected: FAIL — `artifact_dirs` not accepted

**Step 3: Implement**

In `sandbox.py` `SandboxRunner.__init__()`, add `artifact_dirs` parameter:

```python
def __init__(
    self,
    config: SandboxConfig | None,
    workspace: Path,
    editable_files: list[str] | None = None,
    artifact_dirs: list[str] | None = None,
) -> None:
    self.config = config or SandboxConfig(backend="none")
    self.workspace = Path(workspace)
    self.editable_files = editable_files or []
    self.artifact_dirs = artifact_dirs or []
    self._native = ExperimentRunner(workspace=workspace)
    self._cached_hash: str | None = None
```

In `_docker_run()`, after the editable files mount block (around line 91), add:

```python
# Mount artifact directories as read-write
for d in self.artifact_dirs:
    dpath = self.workspace / d
    dpath.mkdir(parents=True, exist_ok=True)
    cmd.extend(["-v", f"{dpath}:/workspace/{d}:rw"])
```

In `orchestrator.py`, pass artifacts to SandboxRunner (around line 57):

```python
self.runner = SandboxRunner(
    config=config.sandbox,
    workspace=self.workspace,
    editable_files=config.files.editable,
    artifact_dirs=config.files.artifacts,
)
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_sandbox.py -k "mounts_artifacts" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/crucible/sandbox.py src/crucible/orchestrator.py tests/test_sandbox.py
git commit -m "feat: mount artifacts as rw volumes in Docker sandbox"
```

---

### Task 5: Validator — check artifacts paths

**Files:**
- Modify: `src/crucible/validator.py:32-100` (validate_project)
- Test: `tests/test_validator.py` (if exists, else add to existing test)

**Step 1: Write failing test**

```python
def test_validate_warns_artifacts_overlap_with_editable(tmp_path):
    """Validator warns if artifacts path overlaps with editable files."""
    crucible_dir = tmp_path / ".crucible"
    crucible_dir.mkdir()
    (crucible_dir / "config.yaml").write_text("""
name: test
files:
  editable: ["main.py"]
  artifacts: ["main.py"]
commands:
  run: "echo 'score: 1'"
  eval: "echo 'score: 1'"
metric:
  name: score
  direction: maximize
""")
    (crucible_dir / "program.md").write_text("test")
    (tmp_path / "main.py").write_text("print('score: 1')")

    from crucible.validator import validate_project
    results = validate_project(tmp_path)
    artifact_results = [r for r in results if "Artifact" in r.name]
    assert any(not r.passed for r in artifact_results)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_validator.py -k "artifacts" -v`
Expected: FAIL — no artifacts validation exists

**Step 3: Implement**

In `validator.py` `validate_project()`, after the files existence checks (around line 79), add:

```python
# Check artifacts don't overlap with other file categories
if config.files.artifacts:
    other_files = set(config.files.editable + config.files.readonly + config.files.hidden)
    overlap = set(config.files.artifacts) & other_files
    if overlap:
        results.append(CheckResult(
            "Artifacts", False,
            f"Artifacts overlap with other file categories: {', '.join(overlap)}"
        ))
    else:
        results.append(CheckResult("Artifacts", True, f"Persistent dirs: {', '.join(config.files.artifacts)}"))
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_validator.py -k "artifacts" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/crucible/validator.py tests/test_validator.py
git commit -m "feat: validator checks artifacts path overlaps"
```

---

### Task 6: End-to-end verification

**Step 1: Update optimize-2048 config to include artifacts**

Add to `src/crucible/examples/optimize-2048/.crucible/config.yaml`:

```yaml
files:
  editable:
    - "strategy.py"
  readonly:
    - "game.py"
    - "view.html"
  hidden:
    - "evaluate.py"
  artifacts:
    - "artifacts/"
```

**Step 2: Run all tests**

Run: `uv run pytest -v`
Expected: All tests pass

**Step 3: Validate the example project**

Run: `cd src/crucible/examples/optimize-2048 && crucible validate`
Expected: All checks pass, including new Artifacts check

**Step 4: Reinstall crucible**

Run: `uv tool install --force --reinstall .`

**Step 5: Commit**

```bash
git add src/crucible/examples/optimize-2048/.crucible/config.yaml
git commit -m "feat: add artifacts support to optimize-2048 example"
```
