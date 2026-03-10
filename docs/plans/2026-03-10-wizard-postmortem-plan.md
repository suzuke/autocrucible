# Wizard + Postmortem Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `crucible wizard` (AI-powered experiment scaffolding) and `crucible postmortem` (AI-powered experiment analysis) commands.

**Architecture:** Two independent modules (`wizard.py`, `postmortem.py`) that use Claude Agent SDK for AI features. Both are pure additions — no changes to the core orchestrator loop. CLI integration via new subcommands in `cli.py`.

**Tech Stack:** Python, Click (CLI), claude_agent_sdk (AI calls), existing results.py/git_manager.py for data access.

---

### Task 1: Postmortem Data Layer — `postmortem.py` core

Build the non-AI data analysis layer first, since it's self-contained and testable without mocking Claude.

**Files:**
- Create: `src/crucible/postmortem.py`
- Test: `tests/test_postmortem.py`

**Step 1: Write failing tests for data layer**

```python
# tests/test_postmortem.py
import subprocess
from pathlib import Path

from crucible.postmortem import PostmortemAnalyzer, PostmortemReport
from crucible.results import ResultsLog


def _make_results_tsv(path: Path, records: list[tuple]) -> None:
    """Helper: write results.tsv with header + records."""
    lines = ["commit\tmetric_value\tstatus\tdescription"]
    for commit, metric, status, desc in records:
        lines.append(f"{commit}\t{metric}\t{status}\t{desc}")
    path.write_text("\n".join(lines) + "\n")


def _setup_repo(tmp_path: Path) -> None:
    """Helper: init git repo with a few commits."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "solution.py").write_text("v = 0")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)


def test_postmortem_summary_stats(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("aaa1111", "10.0", "keep", "baseline"),
        ("bbb2222", "8.0", "discard", "tried something"),
        ("ccc3333", "15.0", "keep", "improved"),
        ("ddd4444", "0.0", "crash", "broke it"),
    ])
    analyzer = PostmortemAnalyzer(workspace=tmp_path, direction="maximize")
    report = analyzer.analyze()
    assert report.total == 4
    assert report.kept == 2
    assert report.discarded == 1
    assert report.crashed == 1
    assert report.best_metric == 15.0
    assert report.best_commit == "ccc3333"


def test_postmortem_failure_streaks(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("a", "10.0", "keep", "baseline"),
        ("b", "0.0", "crash", "crash 1"),
        ("c", "0.0", "crash", "crash 2"),
        ("d", "0.0", "crash", "crash 3"),
        ("e", "20.0", "keep", "recovered"),
    ])
    analyzer = PostmortemAnalyzer(workspace=tmp_path, direction="maximize")
    report = analyzer.analyze()
    assert len(report.failure_streaks) == 1
    streak = report.failure_streaks[0]
    assert streak["start"] == 2  # 1-indexed iteration
    assert streak["length"] == 3


def test_postmortem_trend_data(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("a", "10.0", "keep", "first"),
        ("b", "20.0", "keep", "second"),
    ])
    analyzer = PostmortemAnalyzer(workspace=tmp_path, direction="maximize")
    report = analyzer.analyze()
    assert len(report.trend) == 2
    assert report.trend[0]["iteration"] == 1
    assert report.trend[0]["metric"] == 10.0
    assert report.trend[0]["status"] == "keep"
    assert report.trend[1]["iteration"] == 2


def test_postmortem_minimize_direction(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("a", "10.0", "keep", "first"),
        ("b", "5.0", "keep", "better"),
    ])
    analyzer = PostmortemAnalyzer(workspace=tmp_path, direction="minimize")
    report = analyzer.analyze()
    assert report.best_metric == 5.0
    assert report.best_commit == "b"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_postmortem.py -v`
Expected: FAIL — `ImportError: cannot import name 'PostmortemAnalyzer'`

**Step 3: Implement PostmortemAnalyzer and PostmortemReport**

```python
# src/crucible/postmortem.py
"""Post-mortem analysis for crucible experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from crucible.results import ResultsLog


@dataclass
class PostmortemReport:
    """Data container for postmortem analysis results."""

    total: int = 0
    kept: int = 0
    discarded: int = 0
    crashed: int = 0
    best_metric: Optional[float] = None
    best_commit: Optional[str] = None
    best_description: Optional[str] = None
    trend: list[dict] = field(default_factory=list)
    failure_streaks: list[dict] = field(default_factory=list)
    ai_insights: Optional[str] = None


class PostmortemAnalyzer:
    """Analyzes a completed crucible experiment run."""

    def __init__(self, workspace: Path, direction: str) -> None:
        self.workspace = Path(workspace)
        self.direction = direction
        self.results = ResultsLog(self.workspace / "results.tsv")

    def analyze(self) -> PostmortemReport:
        """Run data-layer analysis. Returns a PostmortemReport."""
        records = self.results.read_all()
        report = PostmortemReport()
        report.total = len(records)
        report.kept = sum(1 for r in records if r.status == "keep")
        report.discarded = sum(1 for r in records if r.status == "discard")
        report.crashed = sum(1 for r in records if r.status == "crash")

        # Best metric
        kept = [r for r in records if r.status == "keep"]
        if kept:
            if self.direction == "minimize":
                best = min(kept, key=lambda r: r.metric_value)
            else:
                best = max(kept, key=lambda r: r.metric_value)
            report.best_metric = best.metric_value
            report.best_commit = best.commit
            report.best_description = best.description

        # Trend data
        for i, r in enumerate(records, 1):
            report.trend.append({
                "iteration": i,
                "metric": r.metric_value,
                "status": r.status,
                "description": r.description,
                "commit": r.commit,
            })

        # Failure streaks
        report.failure_streaks = self._find_failure_streaks(records)

        return report

    def _find_failure_streaks(self, records) -> list[dict]:
        """Find consecutive non-keep runs (crash or discard)."""
        streaks = []
        streak_start = None
        streak_len = 0
        for i, r in enumerate(records):
            if r.status != "keep":
                if streak_start is None:
                    streak_start = i + 1  # 1-indexed
                streak_len += 1
            else:
                if streak_len >= 2:
                    streaks.append({"start": streak_start, "length": streak_len})
                streak_start = None
                streak_len = 0
        if streak_len >= 2:
            streaks.append({"start": streak_start, "length": streak_len})
        return streaks
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_postmortem.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add src/crucible/postmortem.py tests/test_postmortem.py
git commit -m "feat: add postmortem data layer with stats, trends, failure streaks"
```

---

### Task 2: Postmortem ASCII Rendering

Add terminal rendering for the postmortem report (trend chart + summary).

**Files:**
- Modify: `src/crucible/postmortem.py`
- Test: `tests/test_postmortem.py`

**Step 1: Write failing test for render_text**

```python
# Add to tests/test_postmortem.py

def test_render_text_contains_summary(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("aaa1111", "10.0", "keep", "baseline"),
        ("bbb2222", "20.0", "keep", "improved"),
        ("ccc3333", "0.0", "crash", "broke it"),
    ])
    analyzer = PostmortemAnalyzer(workspace=tmp_path, direction="maximize")
    report = analyzer.analyze()
    text = render_text(report)
    assert "3" in text  # total
    assert "2" in text  # kept
    assert "20.0" in text  # best metric
    assert "█" in text  # bar chart


def test_render_text_empty_results(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [])
    analyzer = PostmortemAnalyzer(workspace=tmp_path, direction="maximize")
    report = analyzer.analyze()
    text = render_text(report)
    assert "No iterations" in text
```

Update import: `from crucible.postmortem import PostmortemAnalyzer, PostmortemReport, render_text`

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_postmortem.py::test_render_text_contains_summary -v`
Expected: FAIL — `ImportError: cannot import name 'render_text'`

**Step 3: Implement render_text**

Add to `src/crucible/postmortem.py`:

```python
def render_text(report: PostmortemReport) -> str:
    """Render a PostmortemReport as human-readable terminal text."""
    if report.total == 0:
        return "No iterations recorded."

    lines = []

    # Summary
    lines.append("## Summary")
    best_str = f"{report.best_metric}" if report.best_metric is not None else "N/A"
    lines.append(f"  Best: {best_str} ({report.best_commit or 'N/A'})")
    keep_pct = f"{report.kept}/{report.total} ({100 * report.kept // report.total}%)"
    lines.append(f"  Kept: {keep_pct}  |  Discarded: {report.discarded}  |  Crashed: {report.crashed}")
    lines.append("")

    # Trend bar chart
    lines.append("## Metric Trend")
    max_metric = max((t["metric"] for t in report.trend), default=1)
    if max_metric == 0:
        max_metric = 1
    bar_width = 20
    for t in report.trend:
        filled = int(bar_width * t["metric"] / max_metric) if max_metric else 0
        bar = "█" * filled + "░" * (bar_width - filled)
        star = " ★" if t["commit"] == report.best_commit and t["status"] == "keep" else ""
        desc = t["description"]
        if len(desc) > 40:
            desc = desc[:39] + "…"
        lines.append(
            f"  iter {t['iteration']:>3} {bar} {t['metric']:>10.1f}   "
            f"{t['status']:<8}{star}  {desc}"
        )
    lines.append("")

    # Failure streaks
    if report.failure_streaks:
        lines.append("## Failure Streaks")
        for s in report.failure_streaks:
            end = s["start"] + s["length"] - 1
            lines.append(f"  iter {s['start']}-{end}: {s['length']} consecutive failures")
        lines.append("")

    # AI insights (if present)
    if report.ai_insights:
        lines.append("## Key Insights")
        lines.append(report.ai_insights)
        lines.append("")

    return "\n".join(lines)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_postmortem.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add src/crucible/postmortem.py tests/test_postmortem.py
git commit -m "feat: add postmortem ASCII rendering with bar chart and streaks"
```

---

### Task 3: Postmortem AI Insights Layer

Add Claude-powered analysis that reads experiment data and produces insights.

**Files:**
- Modify: `src/crucible/postmortem.py`
- Test: `tests/test_postmortem.py`

**Step 1: Write failing test with mocked Claude**

```python
# Add to tests/test_postmortem.py
from unittest.mock import patch, AsyncMock


def test_ai_insights_called_with_data(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("a", "10.0", "keep", "baseline"),
        ("b", "20.0", "keep", "improved"),
    ])
    analyzer = PostmortemAnalyzer(workspace=tmp_path, direction="maximize")
    report = analyzer.analyze()

    fake_insights = "1. Good progress from baseline to improved."
    with patch("crucible.postmortem._call_claude_for_insights", return_value=fake_insights):
        analyzer.add_ai_insights(report)

    assert report.ai_insights == fake_insights


def test_ai_insights_prompt_contains_data(tmp_path):
    _setup_repo(tmp_path)
    _make_results_tsv(tmp_path / "results.tsv", [
        ("a", "10.0", "keep", "baseline"),
    ])
    analyzer = PostmortemAnalyzer(workspace=tmp_path, direction="maximize")
    report = analyzer.analyze()

    captured_prompt = None

    def fake_call(prompt):
        nonlocal captured_prompt
        captured_prompt = prompt
        return "insights"

    with patch("crucible.postmortem._call_claude_for_insights", side_effect=fake_call):
        analyzer.add_ai_insights(report)

    assert "10.0" in captured_prompt
    assert "baseline" in captured_prompt
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_postmortem.py::test_ai_insights_called_with_data -v`
Expected: FAIL — `AttributeError: 'PostmortemAnalyzer' has no attribute 'add_ai_insights'`

**Step 3: Implement add_ai_insights and _call_claude_for_insights**

Add to `src/crucible/postmortem.py`:

```python
import asyncio
import json as json_module
import logging
import os

from claude_agent_sdk import ClaudeAgentOptions, AssistantMessage, ResultMessage, TextBlock, query

logger = logging.getLogger(__name__)

# Add these methods to PostmortemAnalyzer class:

    def add_ai_insights(self, report: PostmortemReport) -> None:
        """Call Claude to analyze the experiment and add insights to the report."""
        prompt = self._build_insights_prompt(report)
        report.ai_insights = _call_claude_for_insights(prompt)

    def _build_insights_prompt(self, report: PostmortemReport) -> str:
        """Build the prompt for Claude to analyze experiment results."""
        lines = [
            "Analyze this crucible experiment and provide key insights.",
            "",
            f"Direction: {self.direction}",
            f"Total iterations: {report.total}",
            f"Kept: {report.kept}, Discarded: {report.discarded}, Crashed: {report.crashed}",
            f"Best metric: {report.best_metric} ({report.best_commit})",
            "",
            "## Iteration History",
            "",
        ]
        for t in report.trend:
            lines.append(f"  iter {t['iteration']}: {t['metric']} ({t['status']}) — {t['description']}")

        if report.failure_streaks:
            lines.append("")
            lines.append("## Failure Streaks")
            for s in report.failure_streaks:
                end = s["start"] + s["length"] - 1
                lines.append(f"  iter {s['start']}-{end}: {s['length']} consecutive failures")

        lines.extend([
            "",
            "## Instructions",
            "",
            "Provide 3-5 key insights as numbered items. For each:",
            "- Identify turning points (which change caused qualitative leaps)",
            "- Explain failure patterns (why consecutive crashes or discards)",
            "- Detect plateaus (diminishing returns periods)",
            "- Suggest next directions the agent hasn't tried",
            "",
            "Be concise. One paragraph per insight max.",
        ])
        return "\n".join(lines)


def _call_claude_for_insights(prompt: str) -> str:
    """Call Claude Agent SDK to generate insights. Returns text."""
    try:
        return asyncio.run(_call_claude_async(prompt))
    except Exception as e:
        logger.warning(f"AI insights failed: {e}")
        return f"(AI analysis unavailable: {e})"


async def _call_claude_async(prompt: str) -> str:
    saved = os.environ.pop("CLAUDECODE", None)
    try:
        options = ClaudeAgentOptions(
            system_prompt="You are an experiment analyst. Provide concise, actionable insights about optimization experiment results.",
            permission_mode="bypassPermissions",
            allowed_tools=[],
            cwd=Path.cwd(),
        )
        last_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        last_text = block.text.strip()
        return last_text or "(no insights generated)"
    finally:
        if saved is not None:
            os.environ["CLAUDECODE"] = saved
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_postmortem.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add src/crucible/postmortem.py tests/test_postmortem.py
git commit -m "feat: add AI insights layer to postmortem analyzer"
```

---

### Task 4: Postmortem CLI Subcommand

Wire up the postmortem module to `cli.py`.

**Files:**
- Modify: `src/crucible/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing test**

```python
# Add to tests/test_cli.py

def test_postmortem_no_ai(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "pm1", "--project-dir", str(tmp_path)])
    # Add some fake results
    results_path = tmp_path / "results.tsv"
    with results_path.open("a") as f:
        f.write("abc1234\t0.5\tkeep\tbaseline\n")
        f.write("def5678\t0.3\tkeep\timproved\n")
    result = runner.invoke(main, ["postmortem", "--tag", "pm1", "--project-dir", str(tmp_path), "--no-ai"])
    assert result.exit_code == 0
    assert "Summary" in result.output
    assert "█" in result.output


def test_postmortem_json(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init", "--tag", "pm2", "--project-dir", str(tmp_path)])
    results_path = tmp_path / "results.tsv"
    with results_path.open("a") as f:
        f.write("abc1234\t0.5\tkeep\tbaseline\n")
    result = runner.invoke(main, ["postmortem", "--tag", "pm2", "--project-dir", str(tmp_path), "--no-ai", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total"] == 1
    assert data["kept"] == 1


def test_postmortem_no_results(tmp_path):
    setup_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["postmortem", "--tag", "xxx", "--project-dir", str(tmp_path), "--no-ai"])
    assert result.exit_code != 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::test_postmortem_no_ai -v`
Expected: FAIL — `No such command 'postmortem'`

**Step 3: Add postmortem command to cli.py**

Add to `src/crucible/cli.py` after the `compare` command:

```python
@main.command()
@click.option("--tag", required=True, help="Experiment tag to analyze.")
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--no-ai", is_flag=True, help="Skip AI insights (data only).")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def postmortem(tag: str, project_dir: str, no_ai: bool, as_json: bool) -> None:
    """Analyze a completed experiment run."""
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    results_path = project / "results.tsv"
    if not results_path.exists():
        raise click.ClickException("No results.tsv found. Run an experiment first.")

    from crucible.postmortem import PostmortemAnalyzer, render_text

    analyzer = PostmortemAnalyzer(workspace=project, direction=config.metric.direction)
    report = analyzer.analyze()

    if report.total == 0:
        raise click.ClickException("No iterations recorded.")

    if not no_ai:
        click.echo("Generating AI insights...")
        analyzer.add_ai_insights(report)

    if as_json:
        import json as json_module
        data = {
            "total": report.total,
            "kept": report.kept,
            "discarded": report.discarded,
            "crashed": report.crashed,
            "best_metric": report.best_metric,
            "best_commit": report.best_commit,
            "trend": report.trend,
            "failure_streaks": report.failure_streaks,
            "ai_insights": report.ai_insights,
        }
        click.echo(json_module.dumps(data))
    else:
        click.echo(render_text(report))
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all PASS

**Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: all PASS

**Step 6: Commit**

```bash
git add src/crucible/cli.py tests/test_cli.py
git commit -m "feat: add crucible postmortem CLI command"
```

---

### Task 5: Wizard Core — `wizard.py` with Mocked Claude

Build the wizard's two-phase flow (analyze + generate) with the file writing logic.

**Files:**
- Create: `src/crucible/wizard.py`
- Test: `tests/test_wizard.py`

**Step 1: Write failing tests**

```python
# tests/test_wizard.py
import json
from pathlib import Path
from unittest.mock import patch

from crucible.wizard import ExperimentWizard


MOCK_ANALYZE_RESPONSE = json.dumps({
    "inferred": {
        "name": "sorting-experiment",
        "metric_name": "throughput",
        "metric_direction": "maximize",
        "editable_files": ["solution.py"],
        "timeout_seconds": 60,
    },
    "uncertain": [
        {
            "param": "measurement_method",
            "question": "How should we measure speed?",
            "choices": [
                {"label": "Elements per second", "explanation": "Measures raw throughput"},
                {"label": "Time for 10K elements", "explanation": "Measures latency"},
            ],
        }
    ],
})

MOCK_GENERATE_RESPONSE = json.dumps({
    "files": {
        ".crucible/config.yaml": "name: sorting-experiment\nfiles:\n  editable: [solution.py]\ncommands:\n  run: \"python3 evaluate.py > run.log 2>&1\"\n  eval: \"grep '^throughput:' run.log\"\nmetric:\n  name: throughput\n  direction: maximize\n",
        ".crucible/program.md": "# Sorting Optimization\n\nMaximize throughput of sort function.\n",
        "evaluate.py": "import time\nfrom solution import sort_fn\ndata = list(range(1000, 0, -1))\nstart = time.time()\nresult = sort_fn(data[:])\nend = time.time()\nassert result == sorted(data)\nprint(f'throughput: {len(data)/(end-start):.1f}')\n",
        "solution.py": "def sort_fn(arr):\n    for i in range(len(arr)):\n        for j in range(i+1, len(arr)):\n            if arr[i] > arr[j]:\n                arr[i], arr[j] = arr[j], arr[i]\n    return arr\n",
    },
    "summary": "Metric: throughput (elements/sec, higher = better)\nCorrectness: validates sorted output\nAgent edits: solution.py",
})


def test_wizard_analyze_returns_questions():
    wizard = ExperimentWizard()
    with patch("crucible.wizard._call_claude", return_value=MOCK_ANALYZE_RESPONSE):
        result = wizard.analyze("Write the fastest Python sort without builtin sort")
    assert result["inferred"]["metric_name"] == "throughput"
    assert len(result["uncertain"]) == 1
    assert result["uncertain"][0]["param"] == "measurement_method"


def test_wizard_generate_writes_files(tmp_path):
    wizard = ExperimentWizard()
    decisions = {
        "name": "sorting-experiment",
        "metric_name": "throughput",
        "metric_direction": "maximize",
        "editable_files": ["solution.py"],
        "timeout_seconds": 60,
        "measurement_method": "Elements per second",
    }
    with patch("crucible.wizard._call_claude", return_value=MOCK_GENERATE_RESPONSE):
        summary = wizard.generate(
            description="Write the fastest Python sort",
            decisions=decisions,
            dest=tmp_path,
        )
    assert (tmp_path / ".crucible" / "config.yaml").exists()
    assert (tmp_path / ".crucible" / "program.md").exists()
    assert (tmp_path / "evaluate.py").exists()
    assert (tmp_path / "solution.py").exists()
    assert "throughput" in summary


def test_wizard_generate_creates_gitignore(tmp_path):
    wizard = ExperimentWizard()
    decisions = {"name": "test"}
    with patch("crucible.wizard._call_claude", return_value=MOCK_GENERATE_RESPONSE):
        wizard.generate(description="test", decisions=decisions, dest=tmp_path)
    gi = tmp_path / ".gitignore"
    assert gi.exists()
    assert "results.tsv" in gi.read_text()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wizard.py -v`
Expected: FAIL — `ImportError: cannot import name 'ExperimentWizard'`

**Step 3: Implement ExperimentWizard**

```python
# src/crucible/wizard.py
"""Interactive experiment wizard powered by Claude."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

logger = logging.getLogger(__name__)

ANALYZE_SYSTEM_PROMPT = """\
You are a crucible experiment designer. Given a user's natural language description \
of what they want to optimize, analyze it and return a JSON object with two keys:

1. "inferred": parameters you can confidently determine from the description:
   - name (string): short project name
   - metric_name (string): what to measure
   - metric_direction (string): "minimize" or "maximize"
   - editable_files (list[str]): files the agent should edit
   - timeout_seconds (int): reasonable timeout

2. "uncertain": list of ambiguous decisions. Each has:
   - param (string): parameter name
   - question (string): plain-language question for the user
   - choices (list): 2-3 options, each with "label" and "explanation"

Rules:
- Max 3 uncertain items. If more, pick the 3 most impactful.
- Never ask technical jargon questions. Use plain language.
- Return ONLY valid JSON, no markdown fences.
"""

GENERATE_SYSTEM_PROMPT = """\
You are a crucible experiment generator. Given the user's description and all \
parameter decisions, generate the experiment files.

Return a JSON object with:
1. "files": dict mapping relative file paths to their content:
   - ".crucible/config.yaml": standard crucible config format
   - ".crucible/program.md": clear instructions for the optimization agent
   - "evaluate.py": readonly evaluation harness with correctness validation + metric output
   - The editable source file(s): minimal baseline implementation

2. "summary": 2-3 line human-readable summary of what was configured

Key rules for evaluate.py:
- Must print "metric_name: value" format
- Must include correctness validation (not just performance)
- Must use fixed seeds / deterministic inputs
- Must be tamper-resistant (agent cannot cheat)

Return ONLY valid JSON, no markdown fences.
"""


class ExperimentWizard:
    """Two-phase wizard: analyze description → ask questions → generate files."""

    def analyze(self, description: str) -> dict:
        """Phase 1: Analyze description, return inferred + uncertain params."""
        prompt = f"User wants to optimize:\n\n{description}"
        response = _call_claude(prompt, ANALYZE_SYSTEM_PROMPT)
        return json.loads(response)

    def generate(self, description: str, decisions: dict, dest: Path) -> str:
        """Phase 2: Generate all experiment files and write to dest."""
        prompt = (
            f"User description: {description}\n\n"
            f"Decisions: {json.dumps(decisions)}\n\n"
            "Generate the experiment files."
        )
        response = _call_claude(prompt, GENERATE_SYSTEM_PROMPT)
        result = json.loads(response)

        # Write files
        for rel_path, content in result["files"].items():
            full_path = dest / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)

        # Write .gitignore
        gi = dest / ".gitignore"
        gi_text = gi.read_text() if gi.exists() else ""
        for entry in ["results.tsv", "run.log", "__pycache__/", "*.pyc", ".venv/", "uv.lock"]:
            if entry not in gi_text:
                gi_text += f"{entry}\n"
        gi.write_text(gi_text)

        return result.get("summary", "")


def _call_claude(prompt: str, system_prompt: str = "") -> str:
    """Call Claude Agent SDK and return the last text response."""
    try:
        return asyncio.run(_call_claude_async(prompt, system_prompt))
    except Exception as e:
        logger.error(f"Claude call failed: {e}")
        raise


async def _call_claude_async(prompt: str, system_prompt: str) -> str:
    saved = os.environ.pop("CLAUDECODE", None)
    try:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            permission_mode="bypassPermissions",
            allowed_tools=[],
            cwd=Path.cwd(),
        )
        last_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        last_text = block.text.strip()
        return last_text or "{}"
    finally:
        if saved is not None:
            os.environ["CLAUDECODE"] = saved
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_wizard.py -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add src/crucible/wizard.py tests/test_wizard.py
git commit -m "feat: add wizard core with analyze and generate phases"
```

---

### Task 6: Wizard CLI Subcommand

Wire up the wizard to `cli.py` with interactive flow.

**Files:**
- Modify: `src/crucible/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing tests**

```python
# Add to tests/test_cli.py
from unittest.mock import patch

MOCK_ANALYZE = '{"inferred": {"name": "test", "metric_name": "score", "metric_direction": "maximize", "editable_files": ["solution.py"], "timeout_seconds": 60}, "uncertain": []}'
MOCK_GENERATE = '{"files": {".crucible/config.yaml": "name: test\\nfiles:\\n  editable: [solution.py]\\ncommands:\\n  run: \\"echo ok\\"\\n  eval: \\"echo score: 1\\"\\nmetric:\\n  name: score\\n  direction: maximize", ".crucible/program.md": "Optimize.", "solution.py": "x = 1"}, "summary": "Test experiment"}'


def test_wizard_command_with_describe(tmp_path):
    dest = tmp_path / "my-exp"
    runner = CliRunner()
    with patch("crucible.wizard._call_claude", side_effect=[MOCK_ANALYZE, MOCK_GENERATE]):
        result = runner.invoke(main, [
            "wizard", str(dest), "--describe", "optimize something"
        ])
    assert result.exit_code == 0
    assert (dest / ".crucible" / "config.yaml").exists()
    assert (dest / "solution.py").exists()


def test_wizard_command_interactive(tmp_path):
    dest = tmp_path / "my-exp2"
    runner = CliRunner()
    with patch("crucible.wizard._call_claude", side_effect=[MOCK_ANALYZE, MOCK_GENERATE]):
        result = runner.invoke(main, [
            "wizard", str(dest)
        ], input="optimize a sorting algorithm\n")
    assert result.exit_code == 0
    assert (dest / ".crucible" / "config.yaml").exists()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::test_wizard_command_with_describe -v`
Expected: FAIL — `No such command 'wizard'`

**Step 3: Add wizard command to cli.py**

Add to `src/crucible/cli.py` after the `postmortem` command:

```python
@main.command()
@click.argument("dest", type=click.Path())
@click.option("--describe", default=None, help="Experiment description (skip interactive prompt).")
def wizard(dest: str, describe: str | None) -> None:
    """Generate a new experiment from a natural language description."""
    from crucible.wizard import ExperimentWizard

    wiz = ExperimentWizard()
    dest_path = Path(dest).resolve()

    # Get description
    if describe:
        description = describe
    else:
        description = click.prompt("What do you want to optimize? (describe in plain language)")

    # Phase 1: Analyze
    click.echo("\nAnalyzing your description...")
    try:
        analysis = wiz.analyze(description)
    except Exception as e:
        raise click.ClickException(f"Analysis failed: {e}")

    inferred = analysis.get("inferred", {})
    uncertain = analysis.get("uncertain", [])

    # Phase 2: Ask questions for uncertain params
    decisions = dict(inferred)
    if uncertain:
        click.echo(f"\nGot it! I have {len(uncertain)} question(s):\n")
        for i, item in enumerate(uncertain, 1):
            click.echo(f"[{i}/{len(uncertain)}] {item['question']}\n")
            for j, choice in enumerate(item["choices"], 1):
                click.echo(f"  {j}. {choice['label']} — {choice['explanation']}")
            click.echo()
            pick = click.prompt(f"  Pick", type=click.IntRange(1, len(item["choices"])))
            decisions[item["param"]] = item["choices"][pick - 1]["label"]
            click.echo()

    # Phase 3: Generate
    click.echo("Generating experiment...")
    try:
        summary = wiz.generate(description=description, decisions=decisions, dest=dest_path)
    except Exception as e:
        raise click.ClickException(f"Generation failed: {e}")

    # Write pyproject.toml
    _write_pyproject(dest_path, inferred.get("name", "my-experiment"))

    click.echo(f"\n✓ Generated experiment at {dest_path}\n")
    if summary:
        click.echo(f"  {summary}\n")
    click.echo("Next steps:")
    click.echo(f"  cd {dest_path}")
    click.echo("  crucible init --tag run1")
    click.echo("  crucible run --tag run1")
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all PASS

**Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: all PASS

**Step 6: Commit**

```bash
git add src/crucible/cli.py tests/test_cli.py
git commit -m "feat: add crucible wizard CLI command with interactive flow"
```

---

### Task 7: Final Integration Test + Docs

End-to-end smoke test and help text verification.

**Files:**
- Test: `tests/test_cli.py`

**Step 1: Write integration smoke tests**

```python
# Add to tests/test_cli.py

def test_wizard_help():
    runner = CliRunner()
    result = runner.invoke(main, ["wizard", "--help"])
    assert result.exit_code == 0
    assert "natural language" in result.output.lower() or "description" in result.output.lower()


def test_postmortem_help():
    runner = CliRunner()
    result = runner.invoke(main, ["postmortem", "--help"])
    assert result.exit_code == 0
    assert "--no-ai" in result.output
    assert "--json" in result.output
```

**Step 2: Run to verify pass**

Run: `uv run pytest tests/test_cli.py::test_wizard_help tests/test_cli.py::test_postmortem_help -v`
Expected: PASS

**Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: all PASS

**Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: add wizard and postmortem help text smoke tests"
```
