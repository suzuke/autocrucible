"""Click CLI for crucible — init, run, status, history, and new commands."""

from __future__ import annotations

import functools
import importlib.resources
import logging
import os
import shutil
import subprocess
from pathlib import Path

import json as json_module

import click

from crucible.config import ConfigError, load_config
from crucible.i18n import _
from crucible.preflight import check_claude_cli
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
    help=_("Enable debug logging."), expose_value=False,
    is_eager=True, callback=_verbose_callback,
)


@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False, help=_("Enable debug logging."))
def main(verbose: bool) -> None:
    """crucible — automated experiment loop."""  # noqa: kept as fallback
    handler = logging.StreamHandler()
    handler.setFormatter(_ColorFormatter())
    handler.addFilter(_NoEmptyFilter())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[handler],
    )


@main.command()
@click.argument("dest", type=click.Path())
@click.option("--example", "-e", default=None, help=_("Example name to copy from."))
@click.option("--list", "list_examples", is_flag=True, help=_("List available examples."))
def new(dest: str, example: str | None, list_examples: bool) -> None:
    """Create a new experiment project (from an example or empty scaffold)."""  # noqa: fallback
    ex_dir = _examples_dir()

    if list_examples or (example is None and dest == "."):
        if not ex_dir.exists():
            raise click.ClickException(_("Examples directory not found: {ex_dir}").format(ex_dir=ex_dir))
        examples = sorted(p.name for p in ex_dir.iterdir() if p.is_dir())
        if not examples:
            raise click.ClickException(_("No examples found."))
        click.echo(_("Available examples:"))
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
#   max_iterations: null   # max iterations to run (null = unlimited)

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
        click.echo(_("Created empty project at {dest_path}").format(dest_path=dest_path))
        click.echo(_("Edit .crucible/config.yaml and program.md, then run:"))
        click.echo(f"  cd {dest_path}")
        click.echo("  uv sync          # install experiment dependencies")
        click.echo("  crucible init --tag run1   # auto git-init if needed")
        return

    # Copy from example
    src = ex_dir / example
    if not src.exists():
        examples = sorted(p.name for p in ex_dir.iterdir() if p.is_dir())
        raise click.ClickException(
            _("Example '{example}' not found. Available: {available}").format(
                example=example, available=", ".join(examples))
        )

    dest_path = Path(dest).resolve()
    if dest_path.exists() and any(dest_path.iterdir()):
        raise click.ClickException(_("Destination '{dest_path}' is not empty.").format(dest_path=dest_path))

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

    click.echo(_("Created project from example '{example}' at {dest_path}").format(example=example, dest_path=dest_path))
    click.echo(_("Next steps:"))
    click.echo(f"  cd {dest_path}")
    if extra_deps:
        click.echo("  uv sync          # install experiment dependencies")
    click.echo("  crucible run --tag run1    # auto-inits if needed")


@main.command()
@click.option("--tag", required=True, help=_("Experiment tag / branch suffix."))
@click.option("--project-dir", default=".", help=_("Project root directory."))
def init(tag: str, project_dir: str) -> None:
    """Initialise an experiment branch and results log."""  # noqa: fallback
    project = Path(project_dir).resolve()

    # Auto-initialize git repo if needed
    if not (project / ".git").exists():
        click.echo(_("No git repo found — initializing..."))
        subprocess.run(["git", "init"], cwd=project, check=True,
                       capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=project, check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=project, check=True, capture_output=True,
        )
        click.echo(_("Git repo initialized with initial commit."))

    try:
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    from crucible.agents import create_agent
    from crucible.orchestrator import Orchestrator

    editable = set(config.files.editable)
    if config.constraints.allow_install:
        editable.add("requirements.txt")

    agent = create_agent(
        config.agent,
        system_prompt_file=config.agent.system_prompt,
        hidden_files=set(config.files.hidden),
        editable_files=editable,
    )
    orch = Orchestrator(config=config, workspace=project, tag=tag, agent=agent)
    orch.init()

    # Run setup command if configured
    if config.commands.setup:
        click.echo(_("Running setup: {cmd}").format(cmd=config.commands.setup))
        result = subprocess.run(config.commands.setup, shell=True, cwd=project)
        if result.returncode != 0:
            raise click.ClickException(_("Setup command failed with exit code {code}").format(code=result.returncode))

    click.echo(_("Initialised experiment '{tag}' in {project}").format(tag=tag, project=project))


def _scan_previous_runs(project: Path, current_tag: str, direction: str) -> list[dict]:
    """Scan for previous experiment results and return their best scores."""
    previous = []
    result_files = sorted(project.glob("results-*.jsonl"))
    result_files.extend(sorted(project.glob("results-*.tsv")))
    for results_path in result_files:
        tag = results_path.stem.removeprefix("results-")
        if tag == current_tag:
            continue
        log = ResultsLog(results_path)
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
@click.option("--tag", required=True, help=_("Experiment tag / branch suffix."))
@click.option("--project-dir", default=".", help=_("Project root directory."))
@click.option("--model", default=None, help=_("Claude model to use (e.g. sonnet, opus)."))
@click.option("--timeout", default=600, type=int, help=_("Agent timeout per iteration (seconds)."))
@click.option("--max-iterations", default=None, type=int, help=_("Maximum iterations to run (default: unlimited)."))
@click.option("--no-interactive", is_flag=True, default=False, help=_("Skip interactive prompts (start fresh)."))
@click.option("--profile", is_flag=True, default=False, help=_("Enable token profiling (track prompt breakdown, cache efficiency)."))
@_verbose_option
def run(tag: str, project_dir: str, model: str | None, timeout: int, max_iterations: int | None, no_interactive: bool, profile: bool) -> None:
    """Run the experiment loop until interrupted."""  # noqa: fallback
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    check_claude_cli()

    from crucible.agents import create_agent
    from crucible.orchestrator import Orchestrator

    editable = set(config.files.editable)
    if config.constraints.allow_install:
        editable.add("requirements.txt")

    override_kwargs: dict = {}
    if timeout is not None:
        override_kwargs["timeout"] = timeout
    if model is not None:
        override_kwargs["model"] = model
    agent = create_agent(
        config.agent,
        system_prompt_file=config.agent.system_prompt,
        hidden_files=set(config.files.hidden),
        editable_files=editable,
        **override_kwargs,
    )
    orch = Orchestrator(config=config, workspace=project, tag=tag, agent=agent, profile=profile)

    # Resume if branch exists, otherwise auto-init
    if orch.git.branch_exists(tag):
        orch.resume()
        existing = orch.results.read_all()
        click.echo(_("Resuming experiment '{tag}' ({count} previous iterations)").format(tag=tag, count=len(existing)))
    else:
        # Auto-init: git repo + branch + results + setup
        if not (project / ".git").exists():
            click.echo(_("No git repo found — initializing..."))
            subprocess.run(["git", "init"], cwd=project, check=True,
                           capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=project, check=True,
                           capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=project, check=True, capture_output=True,
            )
            click.echo(_("Git repo initialized with initial commit."))

        # Check for previous runs to fork from
        fork_from = None
        if not no_interactive:
            previous = _scan_previous_runs(project, tag, config.metric.direction)
            if previous:
                click.echo("\n" + _("Found previous experiments:"))
                for i, prev in enumerate(previous, 1):
                    click.echo(
                        f"  {i}) {prev['tag']}  — best: {prev['best_metric']} "
                        f"(commit {prev['best_commit']}, {prev['iterations']} iters, "
                        f"{prev['kept']} kept)"
                    )
                click.echo(_("  {n}) Start fresh").format(n=len(previous) + 1))
                choice = click.prompt(
                    _("Fork from"),
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
                        _("Forking from {tag} best ({metric} @ {commit})...").format(
                            tag=selected["tag"], metric=selected["best_metric"],
                            commit=selected["best_commit"])
                    )

        orch.init(fork_from=fork_from)
        if config.search.strategy == "beam":
            orch.init_beams()

        # Sync venv to match current branch's requirements
        # This ensures a fresh run doesn't inherit packages from a previous run
        is_docker = config.sandbox and config.sandbox.backend != "none"
        if not is_docker and (project / ".venv").exists():
            click.echo(_("Syncing environment..."))
            sync_cmd = "uv sync" if (project / "pyproject.toml").exists() else None
            if not sync_cmd and (project / "requirements.txt").exists():
                sync_cmd = "python3 -m pip install -r requirements.txt"
            if sync_cmd:
                # Use .venv Python so pip installs to the same env that runs experiments
                env = os.environ.copy()
                venv_bin = project / ".venv" / "bin"
                env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
                env["VIRTUAL_ENV"] = str(project / ".venv")
                subprocess.run(sync_cmd, shell=True, cwd=project, env=env, capture_output=True)

        if config.commands.setup:
            click.echo(_("Running setup: {cmd}").format(cmd=config.commands.setup))
            result = subprocess.run(config.commands.setup, shell=True, cwd=project)
            if result.returncode != 0:
                raise click.ClickException(_("Setup command failed with exit code {code}").format(code=result.returncode))
        click.echo(_("Initialised experiment '{tag}' in {project}").format(tag=tag, project=project))

    # Hint: suggest validate if repeat=1 and not yet validated
    validated_marker = project / ".crucible" / ".validated"
    if config.evaluation.repeat == 1 and not validated_marker.exists():
        click.echo(
            _("Tip: Run 'crucible validate' first to check if your metric needs "
              "repeat runs (stochastic experiments may benefit from evaluation.repeat: 3).")
        )

    click.echo(_("Press Ctrl+C to stop gracefully."))
    orch.run_loop(max_iterations=max_iterations)
    click.echo(_("Stopped."))


@main.command()
@click.option("--tag", required=True, help=_("Experiment tag."))
@click.option("--project-dir", default=".", help=_("Project root directory."))
@click.option("--json", "as_json", is_flag=True, help=_("Output as JSON."))
def status(tag: str, project_dir: str, as_json: bool) -> None:
    """Show summary of experiment results."""  # noqa: fallback
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    results = ResultsLog(project / results_filename(tag))
    if not results.path.exists():
        raise click.ClickException(_("No {filename} found. Run 'init --tag {tag}' first.").format(filename=results_filename(tag), tag=tag))

    summary = results.summary()
    best = results.best(config.metric.direction)

    # Compute cost info from usage data
    all_records = results.read_all()
    costs = [
        r.usage.total_cost_usd
        for r in all_records
        if r.usage and r.usage.total_cost_usd is not None
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

    click.echo(_("Experiment: {name}").format(name=config.name))
    click.echo(_("Total: {total}  Kept: {kept}  Discarded: {discarded}  Crashed: {crashed}").format(**summary))
    if best is not None:
        click.echo(_("Best {metric}: {value} (commit {commit})").format(
            metric=config.metric.name, value=best.metric_value, commit=best.commit))

    # Cost line
    if total_cost is not None:
        if budget_max:
            pct = total_cost / budget_max * 100
            click.echo(_("Cost: ${cost:.2f} / ${budget:.2f} ({pct:.0f}%) — {iters} iterations").format(
                cost=total_cost, budget=budget_max, pct=pct, iters=num_iterations))
        else:
            click.echo(_("Cost: ${cost:.2f} — {iters} iterations").format(cost=total_cost, iters=num_iterations))
    else:
        click.echo(_("Cost: unknown (agent backend does not report usage)"))


@main.command()
@click.option("--project-dir", default=".", help=_("Project root directory."))
@click.option("--stability", is_flag=True, help=_("Check metric stability."))
@click.option("--runs", default=5, help=_("Number of runs for stability check."))
def validate(project_dir: str, stability: bool, runs: int) -> None:
    """Validate project configuration and run a test execution."""  # noqa: fallback
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
            click.echo(_("  [PASS] Metric stability: CV = {cv:.1f}% over {runs} runs").format(cv=result.cv, runs=runs))
        else:
            click.echo(_("  [WARN] Metric stability: CV = {cv:.1f}% over {runs} runs").format(cv=result.cv, runs=runs))
            if result.values:
                click.echo(_("         Values: {values}").format(values=result.values))
            click.echo(_("         Consider: fix random seeds or increase sample size"))

    if not all_passed:
        raise click.ClickException(_("Validation failed."))


@main.command()
@click.option("--tag", required=True, help=_("Experiment tag."))
@click.option("--last", default=10, help=_("Number of recent results to show."))
@click.option("--project-dir", default=".", help=_("Project root directory."))
@click.option("--json", "as_json", is_flag=True, help=_("Output as JSON."))
@click.option("--format", "fmt", type=click.Choice(["table", "jsonl"]), default="table", help=_("Output format."))
def history(tag: str, last: int, project_dir: str, as_json: bool, fmt: str) -> None:
    """Show recent experiment results."""  # noqa: fallback
    project = Path(project_dir).resolve()
    results = ResultsLog(project / results_filename(tag))
    if not results.path.exists():
        raise click.ClickException(_("No {filename} found. Run 'init --tag {tag}' first.").format(filename=results_filename(tag), tag=tag))

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
        click.echo(_("No results yet."))
        return

    # Determine available width for description
    try:
        term_width = shutil.get_terminal_size().columns
    except Exception:
        term_width = 80
    fixed_cols = 10 + 1 + 10 + 1 + 10 + 1  # commit + metric + status + spaces
    desc_width = max(20, term_width - fixed_cols)

    click.echo(f"{_('Commit'):<10} {_('Metric'):>10} {_('Status'):<10} {_('Description')}")
    click.echo("-" * min(term_width, 120))
    for r in records:
        desc = r.description
        if len(desc) > desc_width:
            desc = desc[:desc_width - 1] + "…"
        click.echo(f"{r.commit:<10} {r.metric_value:>10.4f} {r.status:<10} {desc}")


@main.command()
@click.argument("tags", nargs=2)
@click.option("--project-dir", default=".", help=_("Project root directory."))
@click.option("--json", "as_json", is_flag=True, help=_("Output as JSON."))
def compare(tags: tuple[str, str], project_dir: str, as_json: bool) -> None:
    """Compare two experiment runs side by side."""  # noqa: fallback
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    comparison = {}

    for tag in tags:
        results_path = project / results_filename(tag)
        if not results_path.exists():
            raise click.ClickException(_("No {filename} found for tag '{tag}'.").format(filename=results_filename(tag), tag=tag))
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
@click.option("--describe", default=None, help=_("Experiment description (skip interactive prompt)."))
def wizard(dest: str, describe: str | None) -> None:
    """Generate a new experiment from a natural language description."""  # noqa: fallback
    from crucible.wizard import ExperimentWizard

    if describe is not None:
        description = describe
    else:
        description = click.prompt(_("What do you want to optimize? Describe your experiment"))

    click.echo(_("Analyzing your description..."))
    wiz = ExperimentWizard()
    try:
        result = wiz.analyze(description)
    except Exception as e:
        raise click.ClickException(_("Analysis failed: {error}").format(error=e))

    inferred = result.get("inferred", {})
    uncertain = result.get("uncertain", [])

    decisions = dict(inferred)
    for item in uncertain:
        choices = item.get("choices", [])
        click.echo(f"\n{item['question']}")
        for i, choice in enumerate(choices, 1):
            click.echo(f"  {i}. {choice['label']} — {choice['explanation']}")
        pick = click.prompt(_("Choose"), type=int, default=1)
        idx = max(0, min(pick - 1, len(choices) - 1))
        decisions[item["param"]] = choices[idx]["label"]

    click.echo(_("Generating experiment..."))
    dest_path = Path(dest).resolve()
    dest_path.mkdir(parents=True, exist_ok=True)
    try:
        summary = wiz.generate(description, decisions, dest_path)
    except Exception as e:
        raise click.ClickException(_("Generation failed: {error}").format(error=e))

    _write_pyproject(dest_path, inferred.get("name", "my-experiment"))

    click.echo("\n" + _("Created experiment at {dest_path}").format(dest_path=dest_path))
    click.echo(_("Summary: {summary}").format(summary=summary))
    click.echo("\n" + _("Next steps:"))
    click.echo(f"  cd {dest_path}")
    click.echo("  uv sync")
    click.echo("  crucible init --tag run1")
    click.echo("  crucible run --tag run1")


def _render_token_profile(results_path: Path, as_json: bool) -> None:
    """Render token profiling analysis from experiment results."""
    from crucible.results import ResultsLog

    records = ResultsLog(results_path).read_all()
    records = [r for r in records if r.status != "baseline"]
    if not records:
        click.echo(_("No iterations to analyze."))
        return

    # Per-iteration table
    rows = []
    section_totals: dict[str, list[int]] = {}
    for r in records:
        u = r.usage
        in_tok = u.input_tokens if u else None
        out_tok = u.output_tokens if u else None
        cache_pct = u.cache_hit_percent() if u else None
        agent_s = r.agent_duration_seconds
        run_s = r.run_duration_seconds
        rows.append((r.iteration, in_tok, out_tok, cache_pct, agent_s, run_s, r.status))

        # Accumulate breakdown
        if u and u.prompt_breakdown:
            for k, v in u.prompt_breakdown.items():
                if k != "total":
                    section_totals.setdefault(k, []).append(v)

    if as_json:
        click.echo(json_module.dumps({
            "iterations": [
                {
                    "iter": it, "input_tokens": it_tok, "output_tokens": ot,
                    "cache_hit_pct": cp, "agent_s": ag, "run_s": rs, "status": st,
                }
                for it, it_tok, ot, cp, ag, rs, st in rows
            ],
            "section_averages": {
                k: sum(v) // len(v) for k, v in section_totals.items()
            } if section_totals else None,
        }))
        return

    # Header
    click.echo("\n" + _("Token Profile ({count} iterations)").format(count=len(records)))
    click.echo("=" * 75)
    click.echo(f"{_('Iter'):>5} {_('In Tok'):>8} {_('Out Tok'):>8} {_('Cache%'):>7} {_('Agent(s)'):>9} {_('Run(s)'):>7} {_('Status'):>8}")
    click.echo("-" * 75)

    for it, in_tok, out_tok, cache_pct, agent_s, run_s, status in rows:
        it_str = str(it or "?")
        in_str = str(in_tok) if in_tok is not None else "-"
        out_str = str(out_tok) if out_tok is not None else "-"
        cache_str = f"{cache_pct}%" if cache_pct is not None else "-"
        agent_str = f"{agent_s:.1f}" if agent_s is not None else "-"
        run_str = f"{run_s:.1f}" if run_s is not None else "-"
        click.echo(f"{it_str:>5} {in_str:>8} {out_str:>8} {cache_str:>7} {agent_str:>9} {run_str:>7} {status:>8}")

    # Averages
    in_tokens = [r.usage.input_tokens for r in records if r.usage and r.usage.input_tokens is not None]
    out_tokens = [r.usage.output_tokens for r in records if r.usage and r.usage.output_tokens is not None]
    if in_tokens:
        click.echo("-" * 75)
        click.echo(f"{'avg':>5} {sum(in_tokens)//len(in_tokens):>8} {sum(out_tokens)//len(out_tokens) if out_tokens else 0:>8}")

    # Section breakdown
    if section_totals:
        click.echo("\n" + _("Prompt Breakdown (avg tokens per section):"))
        total_avg = sum(sum(v) // len(v) for v in section_totals.values())
        sorted_sections = sorted(section_totals.items(), key=lambda x: sum(x[1]) // len(x[1]), reverse=True)
        for name, values in sorted_sections:
            avg = sum(values) // len(values)
            pct = avg * 100 // total_avg if total_avg > 0 else 0
            bar = "\u2588" * (pct // 3)
            click.echo(f"  {name:>20}: {avg:>5} ({pct:>2}%) {bar}")

    # Cache efficiency
    cache_vals = [
        cp for r in records
        if r.usage and (cp := r.usage.cache_hit_percent()) is not None
    ]
    if cache_vals:
        click.echo("\n" + _("Cache Efficiency: avg {pct}% hit rate").format(pct=sum(cache_vals)//len(cache_vals)))

    click.echo()


@main.command()
@click.option("--tag", required=True, help=_("Experiment tag to analyze."))
@click.option("--project-dir", default=".", help=_("Project root directory."))
@click.option("--no-ai", is_flag=True, help=_("Skip AI insights (data only)."))
@click.option("--json", "as_json", is_flag=True, help=_("Output as JSON."))
@click.option("--tokens", is_flag=True, help=_("Show token profiling analysis."))
def postmortem(tag: str, project_dir: str, no_ai: bool, as_json: bool, tokens: bool) -> None:
    """Analyze a completed experiment run."""  # noqa: fallback
    try:
        project = Path(project_dir).resolve()
        config = load_config(project)
    except ConfigError as e:
        raise click.ClickException(str(e))

    from crucible.postmortem import PostmortemAnalyzer, render_text

    results_path = project / results_filename(tag)
    if not results_path.exists():
        raise click.ClickException(_("No {filename} found. Run 'init --tag {tag}' first.").format(filename=results_filename(tag), tag=tag))

    analyzer = PostmortemAnalyzer.from_path(results_path, direction=config.metric.direction)
    report = analyzer.analyze()

    if report.total == 0:
        raise click.ClickException(_("No iterations recorded for this experiment."))

    if tokens:
        _render_token_profile(results_path, as_json)
        return

    if not no_ai:
        click.echo(_("Generating AI insights..."))
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


def _get_current_version() -> str:
    """Return the currently installed version of autocrucible."""
    from importlib.metadata import version
    return version("autocrucible")


def _get_latest_version() -> str | None:
    """Query PyPI for the latest version. Returns None on failure."""
    import json as _json
    import urllib.request

    url = "https://pypi.org/pypi/autocrucible/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read())
            return data["info"]["version"]
    except Exception:
        return None


@main.command()
@click.option("--check", is_flag=True, help=_("Check for updates without installing."))
def update(check: bool) -> None:
    """Update crucible to the latest version."""  # noqa: fallback
    current = _get_current_version()

    latest = _get_latest_version()
    if latest is None:
        raise click.ClickException(_("Failed to check PyPI for updates. Check your network connection."))

    if latest == current:
        click.echo(_("Already up to date (v{version}).").format(version=current))
        return

    if check:
        click.echo(_("Update available: v{current} → v{latest}").format(current=current, latest=latest))
        click.echo(_("Run 'crucible update' to install."))
        return

    # Check that uv is available
    if shutil.which("uv") is None:
        raise click.ClickException(
            _("uv is required for updates. Install it: https://docs.astral.sh/uv/")
        )

    click.echo(_("Updating autocrucible... v{current} → v{latest}").format(current=current, latest=latest))
    result = subprocess.run(
        ["uv", "tool", "upgrade", "autocrucible"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(_("Update failed: {error}").format(error=result.stderr.strip()))

    click.echo(_("Updated to v{version} ✓").format(version=latest))
