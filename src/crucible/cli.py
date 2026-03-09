"""Click CLI for crucible — init, run, status, history, and new commands."""

from __future__ import annotations

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


@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
def main(verbose: bool) -> None:
    """crucible — automated experiment loop."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
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
        (ar_dir / "config.yaml").write_text(
            'name: "my-experiment"\ndescription: ""\n\nfiles:\n  editable:\n    - "solution.py"\n  readonly:\n    - "evaluate.py"\n\ncommands:\n  run: "python3 evaluate.py > run.log 2>&1"\n  eval: "grep \'^metric:\' run.log"\n\nmetric:\n  name: "metric"\n  direction: "minimize"\n'
        )
        (ar_dir / "program.md").write_text("# Experiment Instructions\n\nDescribe the optimization goal and rules here.\n")
        (dest_path / ".gitignore").write_text("results.tsv\nrun.log\n__pycache__/\n*.pyc\n.venv/\nuv.lock\n")
        _write_pyproject(dest_path, "my-experiment")
        click.echo(f"Created empty project at {dest_path}")
        click.echo("Edit .crucible/config.yaml and program.md, then run:")
        click.echo(f"  cd {dest_path}")
        click.echo("  uv sync          # install experiment dependencies")
        click.echo("  git init && git add -A && git commit -m 'initial'")
        click.echo("  crucible init --tag run1")
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

    shutil.copytree(src, dest_path)

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
    click.echo("  git init && git add -A && git commit -m 'initial'")
    click.echo("  crucible init --tag run1")
    click.echo("  crucible run --tag run1")


@main.command()
@click.option("--tag", required=True, help="Experiment tag / branch suffix.")
@click.option("--project-dir", default=".", help="Project root directory.")
def init(tag: str, project_dir: str) -> None:
    """Initialise an experiment branch and results log."""
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    from crucible.agents.claude_code import ClaudeCodeAgent
    from crucible.orchestrator import Orchestrator

    agent = ClaudeCodeAgent()
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
