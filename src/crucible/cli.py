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
from crucible.results import ResultsLog


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
        (dest_path / "evaluate.py").write_text("""\
\"\"\"Evaluation harness (readonly). Measures the metric for the current solution.\"\"\"

from solution import solve


def evaluate():
    result = solve()
    # Replace this with your actual evaluation logic
    metric = abs(result - 0)
    print(f"metric: {metric}")


if __name__ == "__main__":
    evaluate()
""")
        (dest_path / ".gitignore").write_text("results.tsv\nrun.log\n__pycache__/\n*.pyc\n.venv/\nuv.lock\n")
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
    click.echo("  crucible init --tag run1   # auto git-init if needed")
    click.echo("  crucible run --tag run1")


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

    from crucible.agents.claude_code import ClaudeCodeAgent
    from crucible.orchestrator import Orchestrator

    agent = ClaudeCodeAgent(system_prompt_file=config.agent.system_prompt)
    orch = Orchestrator(config=config, workspace=project, tag=tag, agent=agent)
    orch.init()

    # Run setup command if configured
    if config.commands.setup:
        click.echo(f"Running setup: {config.commands.setup}")
        result = subprocess.run(config.commands.setup, shell=True, cwd=project)
        if result.returncode != 0:
            raise click.ClickException(f"Setup command failed with exit code {result.returncode}")

    click.echo(f"Initialised experiment '{tag}' in {project}")


@main.command()
@click.option("--tag", required=True, help="Experiment tag / branch suffix.")
@click.option("--project-dir", default=".", help="Project root directory.")
@click.option("--model", default=None, help="Claude model to use (e.g. sonnet, opus).")
@click.option("--timeout", default=600, type=int, help="Agent timeout per iteration (seconds).")
@_verbose_option
def run(tag: str, project_dir: str, model: str | None, timeout: int) -> None:
    """Run the experiment loop until interrupted."""
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    from crucible.agents.claude_code import ClaudeCodeAgent
    from crucible.orchestrator import Orchestrator

    agent = ClaudeCodeAgent(timeout=timeout, model=model, system_prompt_file=config.agent.system_prompt)
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

    tag_a, tag_b = tags
    col_w = max(len(tag_a), len(tag_b), 12)
    click.echo(f"{'':>16} {tag_a:>{col_w}} {tag_b:>{col_w}}")
    for key in ("iterations", "kept", "discarded", "crashed", "best_metric", "best_commit"):
        va = comparison[tag_a].get(key, "N/A")
        vb = comparison[tag_b].get(key, "N/A")
        label = key.replace("_", " ").title()
        click.echo(f"{label:>16} {str(va):>{col_w}} {str(vb):>{col_w}}")
