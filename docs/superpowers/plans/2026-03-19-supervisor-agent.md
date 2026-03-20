# Supervisor Agent Implementation Plan

> ## Status: IMPLEMENTED — PENDING MERGE
>
> **Branch:** `feature/supervisor-agent` (worktree: `.worktrees/supervisor/`)
>
> **Implementation complete** (2026-03-20): 10 commits, 290 tests pass, 14 new supervisor tests.
> Decided to hold merge — live tests validated the plumbing (trigger → LLM review → JSON parse → log) works correctly, but could not naturally trigger a rollback because test agents made genuine improvements instead of gaming. Supervisor currently adds token cost with no visible benefit on simple experiments. Will merge when a real gaming scenario is encountered (likely on multi-file architecture metric experiments with 20+ iterations).
>
> **To merge:** `cd /Users/suzuke/Documents/Hack/crucible && git merge feature/supervisor-agent`
>
> **New files:** `src/crucible/supervisor.py` (~430 lines), `tests/test_supervisor.py` (14 tests)
> **Modified:** `config.py` (+SupervisorConfig), `git_manager.py` (+cherry_pick, rev_parse), `orchestrator.py` (+set_results_path), `cli.py` (wire SupervisorLoop)
>
> **Live test results:**
> - `test-sorting`: Supervisor triggered 2x, correctly judged genuine, agent improved 254→273 ops/sec
> - `test-regression`: Supervisor triggered 2x, correctly judged genuine (crash = sklearn timeout, not gaming)
> - `test-supervisor-gaming`: 30 iterations, 52.7→83.3 quality_score, supervisor triggered 2x continue — agent did real refactoring, no gaming observed
> - `optimize-nanoclaw-arch`: Agent SDK hook errors caused all-skip, could not test

---

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a supervisor agent that periodically reviews the inner optimization loop, detects metric gaming, rolls back to genuine commits, and updates program.md with warnings.

**Architecture:** `SupervisorLoop` wraps `Orchestrator`, calling `run_one_iteration()` in its own loop. On trigger (plateau or interval), it makes a single-shot Claude LLM call to review recent diffs/results. If gaming is detected: git reset to last genuine commit (or cherry-pick good commits), update program.md with warning, continue. Supervisor never modifies evaluate, source files, or config.

**Tech Stack:** Python 3.12, Claude Agent SDK, dataclasses, JSONL logging

**Spec:** `docs/superpowers/specs/2026-03-19-supervisor-agent-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| **Create:** `src/crucible/supervisor.py` | `SupervisorLoop` class — outer loop, trigger detection, LLM review, git rollback, program.md update, logging |
| **Modify:** `src/crucible/config.py` | Add `SupervisorConfig` dataclass + wire into `Config` |
| **Modify:** `src/crucible/cli.py` | Branch on `supervisor.enabled` to use `SupervisorLoop` |
| **Modify:** `src/crucible/git_manager.py` | Add `cherry_pick()` and `rev_parse()` methods |
| **Modify:** `src/crucible/orchestrator.py` | Add `set_results_path()` for per-round results |
| **Create:** `tests/test_supervisor.py` | All supervisor tests |
| **Modify:** `tests/test_git_manager.py` | Tests for new git methods |

---

### Task 1: `SupervisorConfig` dataclass

**Files:**
- Modify: `src/crucible/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py — append
def test_supervisor_config_defaults():
    from crucible.config import SupervisorConfig
    sc = SupervisorConfig()
    assert sc.enabled is False
    assert sc.trigger == "stall"
    assert sc.stall_threshold == 5
    assert sc.review_interval == 5
    assert sc.max_rounds == 3
    assert sc.model == "claude-sonnet-4-6"


def test_supervisor_config_from_yaml(tmp_path):
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("""
name: test
files:
  editable: ["main.py"]
commands:
  run: "python main.py"
  eval: "echo 1.0"
metric:
  name: score
  direction: maximize
supervisor:
  enabled: true
  trigger: interval
  review_interval: 3
  max_rounds: 5
  model: claude-opus-4-6
""")
    (tmp_path / "main.py").write_text("x = 1")
    from crucible.config import load_config
    cfg = load_config(tmp_path)
    assert cfg.supervisor.enabled is True
    assert cfg.supervisor.trigger == "interval"
    assert cfg.supervisor.review_interval == 3
    assert cfg.supervisor.max_rounds == 5
    assert cfg.supervisor.model == "claude-opus-4-6"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_supervisor_config_defaults tests/test_config.py::test_supervisor_config_from_yaml -v`
Expected: FAIL — `SupervisorConfig` not found

- [ ] **Step 3: Implement SupervisorConfig**

In `src/crucible/config.py`, add dataclass before `Config`:

```python
@dataclass
class SupervisorConfig:
    enabled: bool = False
    trigger: str = "stall"          # "stall" | "interval"
    stall_threshold: int = 5
    review_interval: int = 5
    max_rounds: int = 3
    model: str = "claude-sonnet-4-6"
```

Add field to `Config`:

```python
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
```

Add `_build_supervisor()` helper (same pattern as `_build_sandbox`):

```python
def _build_supervisor(data: dict | None) -> SupervisorConfig:
    if not data:
        return SupervisorConfig()
    trigger = data.get("trigger", "stall")
    if trigger not in ("stall", "interval"):
        raise ConfigError(f"supervisor.trigger must be 'stall' or 'interval', got '{trigger}'")
    return SupervisorConfig(**{
        k: v for k, v in data.items()
        if k in {f.name for f in fields(SupervisorConfig)}
    })
```

In `return Config(...)` (line 234), add:

```python
        supervisor=_build_supervisor(raw.get("supervisor")),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/crucible/config.py tests/test_config.py
git commit -m "feat: add SupervisorConfig dataclass"
```

---

### Task 2: GitManager — `cherry_pick()` and `rev_parse()`

**Files:**
- Modify: `src/crucible/git_manager.py`
- Modify: `tests/test_git_manager.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_git_manager.py — append

def test_cherry_pick_single(tmp_path):
    """Cherry-pick a commit from history."""
    gm = setup_git_repo(tmp_path)  # returns GitManager
    gm.create_branch("test")

    (tmp_path / "a.txt").write_text("a")
    gm.commit("add a")
    commit_a = gm.rev_parse("HEAD")  # full 40-char hash

    (tmp_path / "b.txt").write_text("b")
    gm.commit("add b")

    # Reset back to before A, then cherry-pick A
    gm.reset_to_commit("HEAD~2")
    assert not (tmp_path / "a.txt").exists()
    gm.cherry_pick([commit_a])
    assert (tmp_path / "a.txt").exists()
    assert not (tmp_path / "b.txt").exists()


def test_rev_parse(tmp_path):
    """rev_parse returns full 40-char commit hash."""
    gm = setup_git_repo(tmp_path)
    full_hash = gm.rev_parse("HEAD")
    assert len(full_hash) == 40
    assert full_hash.startswith(gm.head())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_git_manager.py::test_cherry_pick_single tests/test_git_manager.py::test_rev_parse -v`
Expected: FAIL — methods not found

- [ ] **Step 3: Implement**

In `src/crucible/git_manager.py`:

```python
def cherry_pick(self, commits: list[str]) -> None:
    """Cherry-pick commits onto current HEAD, in order."""
    for c in commits:
        self._run("cherry-pick", "--no-edit", c)

def rev_parse(self, ref: str) -> str:
    """Resolve a ref to a full 40-char commit hash."""
    return self._run("rev-parse", ref)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_git_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/crucible/git_manager.py tests/test_git_manager.py
git commit -m "feat: add cherry_pick and rev_parse to GitManager"
```

---

### Task 3: Orchestrator — `set_results_path()`

**Files:**
- Modify: `src/crucible/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator.py — append

def test_swap_results_log(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()

    new_path = tmp_path / "results-test-round-2.jsonl"
    orch.set_results_path(new_path)

    assert orch.results.path == new_path
    assert new_path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py::test_swap_results_log -v`
Expected: FAIL — no `set_results_path`

- [ ] **Step 3: Implement**

In `src/crucible/orchestrator.py`, add to `Orchestrator`:

```python
def set_results_path(self, path: Path) -> None:
    """Swap results log to a new file (used by supervisor per-round)."""
    self.results = ResultsLog(path)
    self.results.init()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator.py::test_swap_results_log -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/crucible/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add set_results_path to Orchestrator"
```

---

### Task 4: SupervisorLoop — core loop + trigger + logging (no LLM)

**Files:**
- Create: `src/crucible/supervisor.py`
- Create: `tests/test_supervisor.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_supervisor.py
import json
import subprocess
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from crucible.supervisor import SupervisorLoop
from crucible.orchestrator import Orchestrator
from crucible.config import (
    Config, FilesConfig, CommandsConfig, MetricConfig,
    ConstraintsConfig, AgentConfig, ContextWindowConfig, GitConfig,
    SupervisorConfig,
)
from crucible.results import ExperimentRecord


def make_config(**supervisor_overrides):
    sup = SupervisorConfig(enabled=True, stall_threshold=3, max_rounds=2,
                           **supervisor_overrides)
    return Config(
        name="test",
        files=FilesConfig(editable=["train.py"], readonly=["prepare.py"]),
        commands=CommandsConfig(
            run="python3 train.py > run.log 2>&1",
            eval="grep '^score:' run.log",
        ),
        metric=MetricConfig(name="score", direction="maximize"),
        constraints=ConstraintsConfig(timeout_seconds=60, max_retries=3),
        agent=AgentConfig(context_window=ContextWindowConfig()),
        git=GitConfig(),
        supervisor=sup,
    )


def setup_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"],
                    cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"],
                    cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "train.py").write_text("x = 1")
    (tmp_path / "prepare.py").write_text("# readonly")
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "program.md").write_text("You are a researcher.")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"],
                    cwd=tmp_path, check=True, capture_output=True)


def test_stall_trigger(tmp_path):
    """Supervisor triggers review after stall_threshold consecutive non-keeps."""
    setup_repo(tmp_path)
    cfg = make_config(stall_threshold=3)
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()

    loop = SupervisorLoop(orch, cfg.supervisor, cfg)

    for i in range(3):
        orch.results.log(ExperimentRecord(
            commit=f"abc{i}", metric_value=float(i),
            status="discard", description=f"attempt {i}",
        ))

    assert loop._should_trigger() is True


def test_no_trigger_when_improving(tmp_path):
    """No trigger when last iteration was a keep."""
    setup_repo(tmp_path)
    cfg = make_config(stall_threshold=3)
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()

    loop = SupervisorLoop(orch, cfg.supervisor, cfg)

    orch.results.log(ExperimentRecord(
        commit="abc0", metric_value=1.0, status="discard", description="d",
    ))
    orch.results.log(ExperimentRecord(
        commit="abc1", metric_value=2.0, status="keep", description="k",
    ))

    assert loop._should_trigger() is False


def test_max_rounds_stop(tmp_path):
    """Supervisor stops intervening after max_rounds."""
    setup_repo(tmp_path)
    cfg = make_config(max_rounds=2)
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()

    loop = SupervisorLoop(orch, cfg.supervisor, cfg)
    loop._round = 2

    assert loop._has_rounds_remaining() is False


def test_log_decision(tmp_path):
    """Decision is logged to JSONL and Markdown."""
    setup_repo(tmp_path)
    cfg = make_config()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()

    loop = SupervisorLoop(orch, cfg.supervisor, cfg)
    log_dir = loop._log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    loop._round = 1

    decision = {
        "action": "continue",
        "reasoning": "All changes look genuine",
        "quality_assessment": "genuine",
    }
    loop._log_decision(decision)

    # Check JSONL
    jsonl = log_dir / "supervisor-decisions.jsonl"
    assert jsonl.exists()
    entry = json.loads(jsonl.read_text().strip())
    assert entry["action"] == "continue"
    assert entry["round"] == 1

    # Check Markdown
    md = log_dir / "round-1-review.md"
    assert md.exists()
    assert "genuine" in md.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_supervisor.py -v`
Expected: FAIL — `crucible.supervisor` not found

- [ ] **Step 3: Implement SupervisorLoop skeleton**

Create `src/crucible/supervisor.py`:

```python
"""Supervisor agent — reviews inner loop, detects gaming, rolls back."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from crucible.config import Config, SupervisorConfig
from crucible.orchestrator import Orchestrator
from crucible.results import ExperimentRecord

logger = logging.getLogger(__name__)

_SUPERVISOR_SYSTEM_PROMPT = """\
You are a code review supervisor for an autonomous optimization experiment.

{objective}

You receive experiment state and must output ONLY valid JSON:

{{
  "action": "continue" | "rollback",
  "reasoning": "your full analysis of the diffs and results",
  "quality_assessment": "genuine" | "mixed" | "gaming",
  "gaming_pattern": "description of gaming pattern if detected, else empty string",
  "rollback_to": "full commit hash to reset to, or empty string",
  "cherry_pick": ["commit hashes to cherry-pick after reset, or empty list"],
  "program_md_warning": "warning text to append to program.md, or empty string"
}}
"""

_DEFAULT_OBJECTIVE = (
    "Ensure changes are genuine quality improvements, not metric gaming. "
    "Moving code (deleting from original location) is reasonable. "
    "Copying code (original still exists) is gaming."
)


class SupervisorLoop:
    """Outer loop that wraps Orchestrator with periodic LLM review."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        supervisor_config: SupervisorConfig,
        config: Config,
    ) -> None:
        self.orch = orchestrator
        self.sup_config = supervisor_config
        self.config = config
        self._round = 0
        self._last_decision: dict | None = None
        self._log_dir = self.orch.workspace / "logs" / "supervisor"

    # --- Trigger detection ---

    def _should_trigger(self) -> bool:
        if self.sup_config.trigger == "stall":
            streak = self.orch._count_plateau_streak()
            return streak >= self.sup_config.stall_threshold
        elif self.sup_config.trigger == "interval":
            total = len(self.orch.results.read_all())
            return total > 0 and total % self.sup_config.review_interval == 0
        return False

    def _has_rounds_remaining(self) -> bool:
        return self._round < self.sup_config.max_rounds

    # --- Main loop ---

    def run(self, max_iterations: int | None = None) -> None:
        """Main supervisor loop — replaces orchestrator.run_loop()."""
        if max_iterations is None:
            max_iterations = self.config.constraints.max_iterations

        max_retries = self.config.constraints.max_retries
        session_count = 0
        self._log_dir.mkdir(parents=True, exist_ok=True)

        try:
            while True:
                if max_iterations is not None and session_count >= max_iterations:
                    logger.info(f"Reached max iterations ({max_iterations}), stopping.")
                    break

                logger.info(f"--- iter {self.orch._iteration + 1} (round {self._round + 1}) ---")
                status = self.orch.run_one_iteration()
                session_count += 1

                best = self.orch.results.best(self.config.metric.direction)
                best_str = f"{best.metric_value}" if best else "N/A"
                logger.info(
                    f"[iter {self.orch._iteration}] {status} | "
                    f"best {self.config.metric.name}: {best_str}"
                )

                if status == "budget_exceeded":
                    logger.warning("Budget limit reached, stopping.")
                    break

                if status in ("skip", "violation"):
                    self.orch._consecutive_skips += 1
                else:
                    self.orch._consecutive_skips = 0

                if self.orch._consecutive_failures >= max_retries:
                    logger.warning(f"{max_retries} consecutive failures, stopping.")
                    break
                if self.orch._consecutive_skips >= max_retries:
                    logger.warning(f"{max_retries} consecutive skips, stopping.")
                    break

                # Check supervisor trigger
                if self._should_trigger() and self._has_rounds_remaining():
                    self._run_review()

        except KeyboardInterrupt:
            logger.info(f"Stopped after {self.orch._iteration} iterations.")

    # --- Review cycle ---

    def _run_review(self) -> None:
        self._round += 1
        logger.info(f"=== Supervisor Review — Round {self._round} ===")

        context = self._build_review_context()
        decision = self._call_supervisor_llm(context)

        if decision is None:
            logger.warning("Supervisor LLM returned no decision, continuing.")
            return

        self._log_decision(decision)
        self._last_decision = decision

        if decision.get("action") == "rollback":
            self._execute_rollback(decision)

    def _build_review_context(self) -> dict:
        records = self.orch.results.read_all()
        best = self.orch.results.best(self.config.metric.direction)

        recent = records[-10:]
        results_summary = [
            {
                "iteration": r.iteration,
                "metric": r.metric_value,
                "status": r.status,
                "description": r.description,
                "commit": r.commit,
            }
            for r in recent
        ]

        # Full diff of last keep
        last_keep_diff = None
        for r in reversed(records):
            if r.status == "keep":
                try:
                    import subprocess as sp
                    diff_result = sp.run(
                        ["git", "diff", f"{r.commit}~1..{r.commit}"],
                        cwd=self.orch.workspace,
                        capture_output=True, text=True, check=True,
                    )
                    last_keep_diff = diff_result.stdout
                except Exception:
                    pass
                break

        program_md = self.orch.workspace / ".crucible" / "program.md"
        program_content = program_md.read_text() if program_md.exists() else None

        return {
            "best_metric": best.metric_value if best else None,
            "best_commit": self.orch.git.rev_parse(best.commit) if best else None,
            "results_summary": results_summary,
            "last_keep_diff": last_keep_diff,
            "last_decision": self._last_decision,
            "program_md_content": program_content,
        }

    # --- LLM call (placeholder for Task 5) ---

    def _call_supervisor_llm(self, context: dict) -> dict | None:
        raise NotImplementedError("LLM call not yet implemented")

    # --- Rollback execution (placeholder for Task 6) ---

    def _execute_rollback(self, decision: dict) -> None:
        raise NotImplementedError("Rollback not yet implemented")

    # --- Logging ---

    def _log_decision(self, decision: dict) -> None:
        jsonl_path = self._log_dir / "supervisor-decisions.jsonl"
        entry = {
            "round": self._round,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": decision.get("action"),
            "quality_assessment": decision.get("quality_assessment"),
            "reasoning": decision.get("reasoning"),
        }
        if decision.get("action") == "rollback":
            entry["rollback_to"] = decision.get("rollback_to")
            entry["cherry_pick"] = decision.get("cherry_pick", [])
            entry["program_md_updated"] = bool(decision.get("program_md_warning"))
            entry["gaming_pattern"] = decision.get("gaming_pattern")

        with open(jsonl_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        md_path = self._log_dir / f"round-{self._round}-review.md"
        md_path.write_text(self._render_review_md(decision))

    def _render_review_md(self, decision: dict) -> str:
        lines = [f"# Supervisor Review — Round {self._round}", ""]
        lines.append("## Analysis")
        lines.append(decision.get("reasoning", "(no reasoning)"))
        lines.append("")
        lines.append(f"## Quality Assessment: {decision.get('quality_assessment', 'unknown')}")
        lines.append("")
        lines.append(f"## Action: {decision.get('action', 'unknown')}")

        if decision.get("action") == "rollback":
            lines.append(f"\nRollback to: {decision.get('rollback_to', 'N/A')}")
            if decision.get("cherry_pick"):
                lines.append(f"Cherry-pick: {', '.join(decision['cherry_pick'])}")
            if decision.get("gaming_pattern"):
                lines.append(f"\n### Gaming Pattern\n{decision['gaming_pattern']}")
            if decision.get("program_md_warning"):
                lines.append(f"\n### program.md Warning\n{decision['program_md_warning']}")

        return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_supervisor.py -v`
Expected: PASS (4 tests: trigger, no trigger, max rounds, log)

- [ ] **Step 5: Commit**

```bash
git add src/crucible/supervisor.py tests/test_supervisor.py
git commit -m "feat: SupervisorLoop skeleton — trigger, logging, review context"
```

---

### Task 5: Supervisor LLM call

**Files:**
- Modify: `src/crucible/supervisor.py`
- Modify: `tests/test_supervisor.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_supervisor.py — append

def test_call_supervisor_llm_returns_decision(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()
    loop = SupervisorLoop(orch, cfg.supervisor, cfg)

    mock_decision = {
        "action": "continue",
        "reasoning": "Genuine progress",
        "quality_assessment": "genuine",
        "gaming_pattern": "",
        "rollback_to": "",
        "cherry_pick": [],
        "program_md_warning": "",
    }

    with patch("crucible.supervisor._call_claude_async") as mock_llm:
        mock_llm.return_value = json.dumps(mock_decision)
        result = loop._call_supervisor_llm({"best_metric": 10.0})

    assert result["action"] == "continue"
    assert result["quality_assessment"] == "genuine"


def test_call_supervisor_llm_retry_on_bad_json(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()
    loop = SupervisorLoop(orch, cfg.supervisor, cfg)

    with patch("crucible.supervisor._call_claude_async") as mock_llm:
        mock_llm.side_effect = ["not json", "still not json"]
        result = loop._call_supervisor_llm({"best_metric": 10.0})

    assert result is None
    assert mock_llm.call_count == 2


def test_call_supervisor_llm_extracts_json_from_markdown(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()
    loop = SupervisorLoop(orch, cfg.supervisor, cfg)

    wrapped = '```json\n{"action": "continue", "reasoning": "ok", "quality_assessment": "genuine"}\n```'

    with patch("crucible.supervisor._call_claude_async") as mock_llm:
        mock_llm.return_value = wrapped
        result = loop._call_supervisor_llm({"best_metric": 10.0})

    assert result["action"] == "continue"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_supervisor.py -k "call_supervisor" -v`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement**

In `src/crucible/supervisor.py`, add module-level function:

```python
async def _call_claude_async(prompt: str, system_prompt: str, model: str) -> str:
    """Single-shot Claude Agent SDK call."""
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    saved = os.environ.pop("CLAUDECODE", None)
    try:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            permission_mode="bypassPermissions",
            allowed_tools=[],
            model=model,
            cwd=Path.cwd(),
        )
        last_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        last_text = block.text.strip()
        return last_text or ""
    finally:
        if saved is not None:
            os.environ["CLAUDECODE"] = saved
```

Replace `_call_supervisor_llm` placeholder in `SupervisorLoop`:

```python
def _call_supervisor_llm(self, context: dict) -> dict | None:
    objective = self._load_objective()
    system_prompt = _SUPERVISOR_SYSTEM_PROMPT.format(objective=objective)
    prompt = json.dumps(context, indent=2, default=str)

    for attempt in range(2):
        try:
            raw = asyncio.run(
                _call_claude_async(prompt, system_prompt, self.sup_config.model)
            )
            json_str = _extract_json(raw)
            decision = json.loads(json_str)
            if "action" not in decision:
                raise ValueError("Missing 'action' field")
            return decision
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Supervisor JSON error (attempt {attempt + 1}): {e}")
            if attempt == 0:
                prompt += (
                    f"\n\nPrevious response was invalid JSON: {e}. "
                    "Respond with valid JSON only."
                )
    return None

def _load_objective(self) -> str:
    obj_path = self.orch.workspace / ".crucible" / "supervisor_objective.md"
    if obj_path.exists():
        return obj_path.read_text().strip()
    return _DEFAULT_OBJECTIVE
```

Add module-level helper:

```python
def _extract_json(text: str) -> str:
    """Extract JSON object from text that may contain markdown fences."""
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return text[start:end + 1]
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_supervisor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/crucible/supervisor.py tests/test_supervisor.py
git commit -m "feat: supervisor LLM call with JSON parsing and retry"
```

---

### Task 6: Rollback execution — git reset, cherry-pick, program.md update

**Files:**
- Modify: `src/crucible/supervisor.py`
- Modify: `tests/test_supervisor.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_supervisor.py — append

def test_rollback_resets_to_commit(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()
    loop = SupervisorLoop(orch, cfg.supervisor, cfg)

    # Make commits
    (tmp_path / "train.py").write_text("x = 2")
    orch.git.commit("iter 1")
    target = orch.git.rev_parse("HEAD")

    (tmp_path / "train.py").write_text("x = 3")
    orch.git.commit("iter 2")

    decision = {
        "action": "rollback",
        "reasoning": "gaming",
        "quality_assessment": "gaming",
        "gaming_pattern": "copy-paste",
        "rollback_to": target,
        "cherry_pick": [],
        "program_md_warning": "",
    }
    loop._execute_rollback(decision)
    assert orch.git.rev_parse("HEAD") == target


def test_rollback_updates_program_md(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()
    loop = SupervisorLoop(orch, cfg.supervisor, cfg)

    original_md = (tmp_path / ".crucible" / "program.md").read_text()

    decision = {
        "action": "rollback",
        "reasoning": "gaming",
        "quality_assessment": "gaming",
        "gaming_pattern": "function duplication",
        "rollback_to": "",
        "cherry_pick": [],
        "program_md_warning": "DO NOT copy functions across files.",
    }
    loop._round = 1
    loop._execute_rollback(decision)

    updated = (tmp_path / ".crucible" / "program.md").read_text()
    assert "DO NOT copy functions across files." in updated
    assert original_md in updated  # Original content preserved


def test_rollback_starts_new_round(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()
    loop = SupervisorLoop(orch, cfg.supervisor, cfg)

    # Log a keep so there's a best to seed
    orch.results.log(ExperimentRecord(
        commit="abc0", metric_value=5.0, status="keep", description="good",
    ))

    loop._round = 1
    decision = {
        "action": "rollback",
        "reasoning": "gaming",
        "quality_assessment": "gaming",
        "gaming_pattern": "",
        "rollback_to": "",
        "cherry_pick": [],
        "program_md_warning": "",
    }
    loop._execute_rollback(decision)

    # Results should be on new file for round 2
    assert "round-2" in str(orch.results.path)
    # Consecutive counters reset
    assert orch._consecutive_failures == 0
    assert orch._consecutive_skips == 0


def test_rollback_adds_context_error(tmp_path):
    setup_repo(tmp_path)
    cfg = make_config()
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()
    loop = SupervisorLoop(orch, cfg.supervisor, cfg)

    decision = {
        "action": "rollback",
        "reasoning": "gaming detected",
        "quality_assessment": "gaming",
        "gaming_pattern": "copy-paste duplication",
        "rollback_to": "",
        "cherry_pick": [],
        "program_md_warning": "",
    }
    loop._round = 1
    loop._execute_rollback(decision)

    # Context should have error about rollback
    assert len(orch.context._errors) == 1
    assert "rollback" in orch.context._errors[0].lower() or "回滾" in orch.context._errors[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_supervisor.py -k "rollback" -v`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement `_execute_rollback`**

Replace placeholder in `SupervisorLoop`:

```python
def _execute_rollback(self, decision: dict) -> None:
    """Reset to genuine commit, update program.md, start new round."""
    rollback_to = decision.get("rollback_to", "")
    cherry_picks = decision.get("cherry_pick", [])
    warning = decision.get("program_md_warning", "")
    gaming_pattern = decision.get("gaming_pattern", "")

    # 1. Git reset
    if rollback_to:
        logger.info(f"Rolling back to {rollback_to[:7]}")
        self.orch.git.reset_to_commit(rollback_to)

    # 2. Cherry-pick good commits
    if cherry_picks:
        logger.info(f"Cherry-picking {len(cherry_picks)} commits")
        try:
            self.orch.git.cherry_pick(cherry_picks)
        except Exception as e:
            logger.warning(f"Cherry-pick failed: {e}")
            try:
                self.orch.git._run("cherry-pick", "--abort")
            except Exception:
                pass

    # 3. Update program.md with warning
    if warning:
        program_path = self.orch.workspace / ".crucible" / "program.md"
        existing = program_path.read_text() if program_path.exists() else ""
        updated = existing.rstrip() + f"\n\n## Supervisor Warning (Round {self._round})\n{warning}\n"
        program_path.write_text(updated)
        self.orch.git.commit(f"supervisor: add gaming warning (round {self._round})")
        logger.info("Updated program.md with gaming warning")

    # 4. Add rollback message to context for inner agent
    msg = (
        f"⟳ SUPERVISOR ROLLBACK — Round {self._round}. "
        f"Your recent changes were identified as metric gaming"
    )
    if gaming_pattern:
        msg += f" ({gaming_pattern})"
    msg += ". Take a completely different approach."
    self.orch.context.add_error(msg)

    # 5. Start new round with fresh results
    self._start_new_round()

def _start_new_round(self) -> None:
    """Start a new results log for the next round."""
    prev_best = self.orch.results.best(self.config.metric.direction)

    new_path = (
        self.orch.workspace
        / f"results-{self.orch.tag}-round-{self._round + 1}.jsonl"
    )
    self.orch.set_results_path(new_path)

    if prev_best:
        self.orch.results.seed_baseline(
            prev_best.metric_value, prev_best.commit, f"round-{self._round}"
        )

    self.orch._consecutive_failures = 0
    self.orch._consecutive_skips = 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_supervisor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/crucible/supervisor.py tests/test_supervisor.py
git commit -m "feat: supervisor rollback — git reset, cherry-pick, program.md warning"
```

---

### Task 7: CLI integration

**Files:**
- Modify: `src/crucible/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — append

def test_run_with_supervisor_enabled(tmp_path, monkeypatch):
    """When supervisor.enabled=true, CLI uses SupervisorLoop."""
    cfg_dir = tmp_path / ".crucible"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("""
name: test
files:
  editable: ["main.py"]
commands:
  run: "python3 main.py"
  eval: "echo 'score: 1.0'"
metric:
  name: score
  direction: maximize
supervisor:
  enabled: true
  stall_threshold: 3
""")
    (cfg_dir / "program.md").write_text("Optimize.")
    (tmp_path / "main.py").write_text("print('score: 1.0')")

    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    monkeypatch.chdir(tmp_path)

    from click.testing import CliRunner
    from crucible.cli import main

    with patch("crucible.cli.SupervisorLoop") as MockLoop, \
         patch("crucible.cli.check_claude_cli"):
        mock_instance = MockLoop.return_value
        runner = CliRunner()
        result = runner.invoke(main, ["run", "--tag", "sup-test", "--project-dir", str(tmp_path), "--no-interactive"])

    MockLoop.assert_called_once()
    mock_instance.run.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_run_with_supervisor_enabled -v`
Expected: FAIL — `SupervisorLoop` not imported

- [ ] **Step 3: Implement CLI integration**

In `src/crucible/cli.py`, add import:

```python
from crucible.supervisor import SupervisorLoop
```

Replace the `orch.run_loop()` call (line 532) with:

```python
    # Run experiment loop
    if config.supervisor.enabled:
        # Hide supervisor_objective.md from inner agent
        if ".crucible/supervisor_objective.md" not in config.files.hidden:
            config.files.hidden.append(".crucible/supervisor_objective.md")
        sup_loop = SupervisorLoop(orch, config.supervisor, config)
        sup_loop.run(max_iterations=max_iterations)
    else:
        orch.run_loop(max_iterations=max_iterations)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/crucible/cli.py tests/test_cli.py
git commit -m "feat: wire SupervisorLoop into CLI run command"
```

---

### Task 8: Integration test — full loop with mocked LLM

**Files:**
- Modify: `tests/test_supervisor.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/test_supervisor.py — append

def test_full_loop_triggers_rollback(tmp_path):
    """Integration: stall triggers review, LLM says rollback, loop continues."""
    setup_repo(tmp_path)
    cfg = make_config(stall_threshold=2, max_rounds=1)
    orch = Orchestrator(cfg, tmp_path, tag="test", agent=MagicMock())
    orch.init()

    loop = SupervisorLoop(orch, cfg.supervisor, cfg)
    baseline_commit = orch.git.rev_parse("HEAD")

    call_count = 0

    def fake_iteration():
        nonlocal call_count
        call_count += 1
        (tmp_path / "train.py").write_text(f"x = {call_count}")
        orch.git.commit(f"iter {call_count}")
        orch.results.log(ExperimentRecord(
            commit=orch.git.head(),
            metric_value=float(call_count),
            status="discard",
            description=f"attempt {call_count}",
            iteration=call_count,
        ))
        orch._iteration = call_count
        if call_count >= 4:
            raise KeyboardInterrupt
        return "discard"

    orch.run_one_iteration = fake_iteration

    rollback_decision = {
        "action": "rollback",
        "reasoning": "Gaming detected",
        "quality_assessment": "gaming",
        "gaming_pattern": "copy-paste",
        "rollback_to": baseline_commit,
        "cherry_pick": [],
        "program_md_warning": "Do not copy functions.",
    }

    with patch("crucible.supervisor._call_claude_async") as mock_llm:
        mock_llm.return_value = json.dumps(rollback_decision)
        loop.run(max_iterations=4)

    assert loop._round == 1
    jsonl = loop._log_dir / "supervisor-decisions.jsonl"
    assert jsonl.exists()
    decisions = [json.loads(l) for l in jsonl.read_text().strip().splitlines()]
    assert decisions[0]["action"] == "rollback"
    assert decisions[0]["gaming_pattern"] == "copy-paste"

    # program.md should have warning
    program = (tmp_path / ".crucible" / "program.md").read_text()
    assert "Do not copy functions." in program
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_supervisor.py::test_full_loop_triggers_rollback -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_supervisor.py
git commit -m "test: supervisor integration test with rollback"
```

---

### Task 9: Run all tests + final cleanup

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 2: Verify `logs/` gitignore covers `logs/supervisor/`**

The existing orchestrator `init()` already adds `logs/` to `.gitignore` (line 123). `logs/supervisor/` is a subdirectory, so it's covered. No changes needed.

- [ ] **Step 3: Final commit if any cleanup**

```bash
git add -A
git commit -m "chore: supervisor agent cleanup"
```

---

## Implementation Notes

**Removed from original spec (v1 → v2 simplification):**
- Evaluate modification (append-only patching, superset check, syntax check)
- Frozen baseline / drift detection
- `branch` and `revert` git strategies (kept only `reset` + `cherry-pick`)
- Evaluate file hooks for supervisor

**Key design decisions:**
1. **Supervisor has no tools** — single-shot JSON via `_call_claude_async` with `allowed_tools=[]`
2. **Orchestrator minimally changed** — only `set_results_path()` added
3. **Results per round** — `results-{tag}-round-{N}.jsonl`, previous best seeded via `seed_baseline()`
4. **program.md as communication channel** — supervisor appends warnings, inner agent reads them
5. **Context error for rollback awareness** — `add_error()` tells the inner agent it was rolled back and why
