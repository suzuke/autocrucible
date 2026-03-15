"""Click CLI for crucible — init, run, status, history, and new commands."""

from __future__ import annotations

import functools
import importlib.resources
import logging
import shutil
import subprocess
from pathlib import Path

import json as json_module

import click

from crucible.config import ConfigError, load_config
from crucible.results import ResultsLog, results_filename


def _examples_dir() -> Path:
    """Return the path to the bundled examples directory.

    Works both in development (source tree) and when installed as a
    global tool (examples are inside the crucible package).
    """
    ref = importlib.resources.files("crucible") / "examples"
    # importlib.resources returns a Traversable; for copytree we need a real Path.
    # With the files API on an installed package, this is already a PosixPath.
    return Path(str(ref))


def _write_pyproject(dest: Path, name: str, extra_deps: list[str] | None = None) -> None:
    """Generate a pyproject.toml for the experiment project.

    Does NOT include crucible as a dependency — crucible is installed
    as a global CLI tool (via `uv tool install`). Only experiment-specific
    dependencies (numpy, torch, etc.) are listed here.
    """
    deps = list(extra_deps or [])
    deps_str = ", ".join(f'"{d}"' for d in deps) if deps else ""

    # Add PyTorch index override if torch is a dependency
    torch_section = ""
    if any("torch" in d for d in deps):
        torch_section = (
            "\n[tool.uv]\n"
            "[[tool.uv.index]]\n"
            'name = "pytorch-cpu"\n'
            'url = "https://download.pytorch.org/whl/cpu"\n'
            "explicit = true\n"
            "\n[tool.uv.sources]\n"
            'torch = { index = "pytorch-cpu" }\n'
        )

    (dest / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
        f'requires-python = ">=3.10"\n'
        f'dependencies = [{deps_str}]\n'
        f'{torch_section}'
    )


# Noisy log messages to suppress (from asyncio, SDK internals, etc.)
_LOG_NOISE = {"Using selector:", "Skipping unknown message type:", "Using bundled Claude Code CLI:"}


class _ColorFormatter(logging.Formatter):
    """Colored log formatter for terminal output."""

    COLORS = {
        logging.DEBUG: "bright_black",
        logging.INFO: "green",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
    }
    LEVEL_LABELS = {
        logging.DEBUG: "DBG",
        logging.INFO: "INF",
        logging.WARNING: "WRN",
        logging.ERROR: "ERR",
    }

    def format(self, record: logging.LogRecord) -> str:
        # Suppress noisy messages
        msg = record.getMessage()
        if any(noise in msg for noise in _LOG_NOISE):
            return ""

        ts = self.formatTime(record, "%H:%M:%S")
        color = self.COLORS.get(record.levelno, "white")
        label = self.LEVEL_LABELS.get(record.levelno, record.levelname)
        styled_label = click.style(label, fg=color, bold=True)
        styled_ts = click.style(ts, fg="bright_black")

        # Highlight iteration results
        if "[iter" in msg:
            if "keep" in msg:
                msg = click.style(msg, fg="green", bold=True)
            elif "discard" in msg:
                msg = click.style(msg, fg="yellow")
            elif "crash" in msg:
                msg = click.style(msg, fg="red")
        elif record.levelno == logging.DEBUG:
            msg = click.style(msg, fg="bright_black")

        return f"{styled_ts} {styled_label} {msg}"


class _NoEmptyFilter(logging.Filter):
    """Filter out records that format to empty strings (suppressed noise)."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(noise in msg for noise in _LOG_NOISE)


def _setup_logging(verbose: bool) -> None:
    """Configure logging level. Safe to call multiple times."""
    root = logging.getLogger()
    if verbose and root.level != logging.DEBUG:
        root.setLevel(logging.DEBUG)
        if not root.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(_ColorFormatter())
            handler.addFilter(_NoEmptyFilter())
            root.addHandler(handler)


def _verbose_callback(ctx: click.Context, param: click.Parameter, value: bool) -> bool:
    """Click callback that configures logging when --verbose is used."""
    if value:
        _setup_logging(True)
    return value


_verbose_option = click.option(
    "--verbose", "-v", is_flag=True, default=False,
    help="Enable debug logging.", expose_value=False,
    is_eager=True, callback=_verbose_callback,
)


@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
def main(verbose: bool) -> None:
    """crucible — automated experiment loop."""
    handler = logging.StreamHandler()
    handler.setFormatter(_ColorFormatter())
    handler.addFilter(_NoEmptyFilter())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[handler],
    )


@main.command()
@click.argument("dest", type=click.Path())
@click.option("--example", "-e", default=None, help="Example name to copy from.")
@click.option("--list", "list_examples", is_flag=True, help="List available examples.")
def new(dest: str, example: str | None, list_examples: bool) -> None:
    """Create a new experiment project (from an example or empty scaffold)."""
    ex_dir = _examples_dir()

    if list_examples or (example is None and dest == "."):
        if not ex_dir.exists():
            raise click.ClickException(f"Examples directory not found: {ex_dir}")
        examples = sorted(p.name for p in ex_dir.iterdir() if p.is_dir())
        if not examples:
            raise click.ClickException("No examples found.")
        click.echo("Available examples:")
        for e in examples:
            click.echo(f"  - {e}")
        return

    if example is None:
        # Scaffold an empty project
        dest_path = Path(dest).resolve()
        dest_path.mkdir(parents=True, exist_ok=True)
        ar_dir = dest_path / ".crucible"
        ar_dir.mkdir(exist_ok=True)
        (ar_dir / "config.yaml").write_text("""\
name: "my-experiment"
description: ""

files:
  editable:
    - "solution.py"
  readonly:
    - "evaluate.py"

commands:
  run: "python3 evaluate.py > run.log 2>&1"
  eval: "grep '^metric:' run.log"
  # setup: "pip install -r requirements.txt"  # one-time setup (run on init)

metric:
  name: "metric"
  direction: "minimize"   # "minimize" or "maximize"

# constraints:
#   timeout_seconds: 600  # kill experiment after this
#   max_retries: 3        # max consecutive failures before stop

# agent:
#   instructions: "program.md"
#   system_prompt: "system.md"  # custom system prompt (optional)
#   context_window:
#     include_history: true
#     history_limit: 20
#     include_best: true
""")
        (ar_dir / "program.md").write_text("""\
# Experiment Instructions

Describe the optimization goal and rules here.

## Goal

Minimize the `metric` value by modifying `solution.py`.

## Rules

- Keep the code correct — the evaluation harness validates output.
- Try one change at a time so you can measure its effect.
""")
        (dest_path / "solution.py").write_text("""\
\"\"\"Editable solution file. Modify this to optimize the metric.\"\"\"


def solve():
    return 42


if __name__ == "__main__":
    print(solve())
""")
        (dest_path / "evaluate.py").write_text('''\
"""Evaluation harness (readonly). Measures the metric for the current solution."""

from solution import solve


def evaluate():
    result = solve()

    # === Correctness Gate ===
    # TODO: Add correctness checks here. If correctness fails,
    # print a worst-case metric value and exit early.
    # Example:
    #   if not verify_correctness(result):
    #       print("metric: 999999")  # worst value for minimize
    #       return

    # === Method Verification ===
    # TODO: Verify the solution uses the required approach.
    # Example:
    #   if not uses_required_algorithm(result):
    #       print("metric: 999999")
    #       return

    # === Performance Measurement ===
    metric = abs(result - 0)
    print(f"metric: {metric}")


if __name__ == "__main__":
    evaluate()
''')
        (dest_path / ".gitignore").write_text("results-*.jsonl\nrun.log\n__pycache__/\n*.pyc\n.venv/\nuv.lock\n")
        _write_pyproject(dest_path, "my-experiment")
        click.echo(f"Created empty project at {dest_path}")
        click.echo("Edit .crucible/config.yaml and program.md, then run:")
        click.echo(f"  cd {dest_path}")
        click.echo("  uv sync          # install experiment dependencies")
        click.echo("  crucible init --tag run1   # auto git-init if needed")
        return

    # Copy from example
    src = ex_dir / example
    if not src.exists():
        examples = sorted(p.name for p in ex_dir.iterdir() if p.is_dir())
        raise click.ClickException(
            f"Example '{example}' not found. Available: {', '.join(examples)}"
        )

    dest_path = Path(dest).resolve()
    if dest_path.exists() and any(dest_path.iterdir()):
        raise click.ClickException(f"Destination '{dest_path}' is not empty.")

    shutil.copytree(src, dest_path, dirs_exist_ok=True)

    # Generate pyproject.toml with platform dependency + any requirements.txt deps
    req_file = dest_path / "requirements.txt"
    extra_deps = []
    if req_file.exists():
        extra_deps = [
            line.strip() for line in req_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        req_file.unlink()  # no longer needed — deps are in pyproject.toml

    project_name = example or "my-experiment"
    _write_pyproject(dest_path, project_name, extra_deps)

    # Ensure .gitignore has uv artifacts
    gi = dest_path / ".gitignore"
    gi_text = gi.read_text() if gi.exists() else ""
    for entry in [".venv/", "uv.lock"]:
        if entry not in gi_text:
            gi_text += f"{entry}\n"
    gi.write_text(gi_text)

    click.echo(f"Created project from example '{example}' at {dest_path}")
    click.echo("Next steps:")
    click.echo(f"  cd {dest_path}")
    if extra_deps:
        click.echo("  uv sync          # install experiment dependencies")
    click.echo("  crucible run --tag run1    # auto-inits if needed")


@main.command()
@click.option("--tag", required=True, help="Experiment tag / branch suffix.")
@click.option("--project-dir", default=".", help="Project root directory.")
def init(tag: str, project_dir: str) -> None:
    """Initialise an experiment branch and results log."""
    project = Path(project_dir).resolve()

    # Auto-initialize git repo if needed
    if not (project / ".git").exists():
        click.echo("No git repo found — initializing...")
        subprocess.run(["git", "init"], cwd=project, check=True,
                       capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=project, check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=project, check=True, capture_output=True,
        )
        click.echo("Git repo initialized with initial commit.")

    try:
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    from crucible.agents import create_agent
    from crucible.orchestrator import Orchestrator

    agent = create_agent(
        config.agent,
        system_prompt_file=config.agent.system_prompt,
        hidden_files=set(config.files.hidden),
    )
    orch = Orchestrator(config=config, workspace=project, tag=tag, agent=agent)
    orch.init()

    # Run setup command if configured
    if config.commands.setup:
        click.echo(f"Running setup: {config.commands.setup}")
        result = subprocess.run(config.commands.setup, shell=True, cwd=project)
        if result.returncode != 0:
            raise click.ClickException(f"Setup command failed with exit code {result.returncode}")

    click.echo(f"Initialised experiment '{tag}' in {project}")


def _scan_previous_runs(project: Path, current_tag: str, direction: str) -> list[dict]:
    """Scan for previous experiment results and return their best scores."""
    previous = []
    for tsv_path in sorted(project.glob("results-*.jsonl")):
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


@main.command()
@click.option("--tag", required=True, help="Experiment tag / branch suffix.")
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--model", default=None, help="Claude model to use (e.g. sonnet, opus).")
@click.option("--timeout", default=600, type=int, help="Agent timeout per iteration (seconds).")
@click.option("--no-interactive", is_flag=True, default=False, help="Skip interactive prompts (start fresh).")
@_verbose_option
def run(tag: str, project_dir: str, model: str | None, timeout: int, no_interactive: bool) -> None:
    """Run the experiment loop until interrupted."""
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    from crucible.agents import create_agent
    from crucible.orchestrator import Orchestrator

    agent = create_agent(
        config.agent,
        timeout=timeout,
        model=model,
        system_prompt_file=config.agent.system_prompt,
        hidden_files=set(config.files.hidden),
    )
    orch = Orchestrator(config=config, workspace=project, tag=tag, agent=agent)

    # Resume if branch exists, otherwise auto-init
    if orch.git.branch_exists(tag):
        orch.resume()
        existing = orch.results.read_all()
        click.echo(f"Resuming experiment '{tag}' ({len(existing)} previous iterations)")
    else:
        # Auto-init: git repo + branch + results + setup
        if not (project / ".git").exists():
            click.echo("No git repo found — initializing...")
            subprocess.run(["git", "init"], cwd=project, check=True,
                           capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=project, check=True,
                           capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=project, check=True, capture_output=True,
            )
            click.echo("Git repo initialized with initial commit.")

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
        if config.commands.setup:
            click.echo(f"Running setup: {config.commands.setup}")
            result = subprocess.run(config.commands.setup, shell=True, cwd=project)
            if result.returncode != 0:
                raise click.ClickException(f"Setup command failed with exit code {result.returncode}")
        click.echo(f"Initialised experiment '{tag}' in {project}")

    click.echo("Press Ctrl+C to stop gracefully.")
    orch.run_loop()
    click.echo("Stopped.")


@main.command()
@click.option("--tag", required=True, help="Experiment tag.")
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def status(tag: str, project_dir: str, as_json: bool) -> None:
    """Show summary of experiment results."""
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    results = ResultsLog(project / results_filename(tag))
    if not results.path.exists():
        raise click.ClickException(f"No {results_filename(tag)} found. Run 'init --tag {tag}' first.")

    summary = results.summary()
    best = results.best(config.metric.direction)

    # Compute cost info from usage data
    all_records = results.read_all()
    costs = [
        r.usage.estimated_cost_usd
        for r in all_records
        if r.usage and r.usage.estimated_cost_usd is not None
    ]
    total_cost = sum(costs) if costs else None
    budget_cfg = config.constraints.budget
    budget_max = budget_cfg.max_cost_usd if budget_cfg else None
    num_iterations = len([r for r in all_records if r.status != "baseline"])

    if as_json:
        cost_data: dict = {}
        if total_cost is not None:
            cost_data["total_cost_usd"] = round(total_cost, 4)
            cost_data["iterations"] = num_iterations
            if budget_max:
                cost_data["budget_usd"] = budget_max
                cost_data["percent_used"] = round(total_cost / budget_max * 100, 1)
        else:
            cost_data["total_cost_usd"] = None

        data = {
            "experiment": config.name,
            **summary,
            "best": {
                "metric": best.metric_value,
                "commit": best.commit,
                "description": best.description,
            } if best else None,
            "cost": cost_data,
        }
        click.echo(json_module.dumps(data))
        return

    click.echo(f"Experiment: {config.name}")
    click.echo(f"Total: {summary['total']}  Kept: {summary['kept']}  "
               f"Discarded: {summary['discarded']}  Crashed: {summary['crashed']}")
    if best is not None:
        click.echo(f"Best {config.metric.name}: {best.metric_value} (commit {best.commit})")

    # Cost line
    if total_cost is not None:
        if budget_max:
            pct = total_cost / budget_max * 100
            click.echo(f"Cost: ${total_cost:.2f} / ${budget_max:.2f} ({pct:.0f}%) — {num_iterations} iterations")
        else:
            click.echo(f"Cost: ${total_cost:.2f} — {num_iterations} iterations")
    else:
        click.echo("Cost: unknown (agent backend does not report usage)")


@main.command()
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--stability", is_flag=True, help="Check metric stability.")
@click.option("--runs", default=5, help="Number of runs for stability check.")
def validate(project_dir: str, stability: bool, runs: int) -> None:
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

    if stability:
        from crucible.validator import check_stability

        config = load_config(project)
        result = check_stability(project, config, runs=runs)
        if result.stable:
            click.echo(f"  [PASS] Metric stability: CV = {result.cv:.1f}% over {runs} runs")
        else:
            click.echo(f"  [WARN] Metric stability: CV = {result.cv:.1f}% over {runs} runs")
            if result.values:
                click.echo(f"         Values: {result.values}")
            click.echo("         Consider: fix random seeds or increase sample size")

    if not all_passed:
        raise click.ClickException("Validation failed.")


@main.command()
@click.option("--tag", required=True, help="Experiment tag.")
@click.option("--last", default=10, help="Number of recent results to show.")
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--format", "fmt", type=click.Choice(["table", "jsonl"]), default="table", help="Output format.")
def history(tag: str, last: int, project_dir: str, as_json: bool, fmt: str) -> None:
    """Show recent experiment results."""
    project = Path(project_dir).resolve()
    results = ResultsLog(project / results_filename(tag))
    if not results.path.exists():
        raise click.ClickException(f"No {results_filename(tag)} found. Run 'init --tag {tag}' first.")

    records = results.read_last(last)

    if fmt == "jsonl":
        from crucible.results import _serialize_record
        for r in records:
            click.echo(_serialize_record(r))
        return

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

    # Determine available width for description
    try:
        term_width = shutil.get_terminal_size().columns
    except Exception:
        term_width = 80
    fixed_cols = 10 + 1 + 10 + 1 + 10 + 1  # commit + metric + status + spaces
    desc_width = max(20, term_width - fixed_cols)

    click.echo(f"{'Commit':<10} {'Metric':>10} {'Status':<10} Description")
    click.echo("-" * min(term_width, 120))
    for r in records:
        desc = r.description
        if len(desc) > desc_width:
            desc = desc[:desc_width - 1] + "…"
        click.echo(f"{r.commit:<10} {r.metric_value:>10.4f} {r.status:<10} {desc}")


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

    comparison = {}

    for tag in tags:
        results_path = project / results_filename(tag)
        if not results_path.exists():
            raise click.ClickException(f"No {results_filename(tag)} found for tag '{tag}'.")
        results = ResultsLog(results_path)
        records = results.read_all()
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

    tag_a, tag_b = tags
    col_w = max(len(tag_a), len(tag_b), 12)
    click.echo(f"{'':>16} {tag_a:>{col_w}} {tag_b:>{col_w}}")
    for key in ("iterations", "kept", "discarded", "crashed", "best_metric", "best_commit"):
        va = comparison[tag_a].get(key, "N/A")
        vb = comparison[tag_b].get(key, "N/A")
        label = key.replace("_", " ").title()
        click.echo(f"{label:>16} {str(va):>{col_w}} {str(vb):>{col_w}}")


@main.command()
@click.argument("dest", type=click.Path())
@click.option("--describe", default=None, help="Experiment description (skip interactive prompt).")
def wizard(dest: str, describe: str | None) -> None:
    """Generate a new experiment from a natural language description."""
    from crucible.wizard import ExperimentWizard

    if describe is not None:
        description = describe
    else:
        description = click.prompt("What do you want to optimize? Describe your experiment")

    click.echo("Analyzing your description...")
    wiz = ExperimentWizard()
    try:
        result = wiz.analyze(description)
    except Exception as e:
        raise click.ClickException(f"Analysis failed: {e}")

    inferred = result.get("inferred", {})
    uncertain = result.get("uncertain", [])

    decisions = dict(inferred)
    for item in uncertain:
        choices = item.get("choices", [])
        click.echo(f"\n{item['question']}")
        for i, choice in enumerate(choices, 1):
            click.echo(f"  {i}. {choice['label']} — {choice['explanation']}")
        pick = click.prompt("Choose", type=int, default=1)
        idx = max(0, min(pick - 1, len(choices) - 1))
        decisions[item["param"]] = choices[idx]["label"]

    click.echo("Generating experiment...")
    dest_path = Path(dest).resolve()
    dest_path.mkdir(parents=True, exist_ok=True)
    try:
        summary = wiz.generate(description, decisions, dest_path)
    except Exception as e:
        raise click.ClickException(f"Generation failed: {e}")

    _write_pyproject(dest_path, inferred.get("name", "my-experiment"))

    click.echo(f"\nCreated experiment at {dest_path}")
    click.echo(f"Summary: {summary}")
    click.echo("\nNext steps:")
    click.echo(f"  cd {dest_path}")
    click.echo("  uv sync")
    click.echo("  crucible init --tag run1")
    click.echo("  crucible run --tag run1")


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

    from crucible.postmortem import PostmortemAnalyzer, render_text

    results_path = project / results_filename(tag)
    if not results_path.exists():
        raise click.ClickException(f"No {results_filename(tag)} found. Run 'init --tag {tag}' first.")

    analyzer = PostmortemAnalyzer.from_path(results_path, direction=config.metric.direction)
    report = analyzer.analyze()

    if report.total == 0:
        raise click.ClickException("No iterations recorded for this experiment.")

    if not no_ai:
        click.echo("Generating AI insights...")
        analyzer.add_ai_insights(report)

    if as_json:
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
