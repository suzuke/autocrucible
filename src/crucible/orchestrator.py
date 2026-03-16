"""Orchestrator — core experiment loop tying all modules together."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from crucible.agents.base import AgentInterface
from crucible.budget import BudgetGuard
from crucible.config import Config
from crucible.context import ContextAssembler
from crucible.git_manager import GitManager
from crucible.guardrails import GuardRails
from crucible.results import ExperimentRecord, ResultsLog, results_filename
from crucible.runner import ExperimentRunner


class Orchestrator:
    """Ties all modules together into the core experiment loop."""

    def __init__(
        self,
        config: Config,
        workspace: Path | str,
        tag: str,
        agent: AgentInterface,
    ) -> None:
        self.config = config
        self.workspace = Path(workspace)
        self.tag = tag
        self.agent = agent

        branch_name = f"{config.git.branch_prefix}/{tag}"

        self.git = GitManager(
            workspace=self.workspace,
            branch_prefix=config.git.branch_prefix,
            tag_failed=config.git.tag_failed,
        )
        self.guardrails = GuardRails(
            editable=config.files.editable,
            readonly=config.files.readonly,
        )
        self.results = ResultsLog(self.workspace / results_filename(tag))
        self.runner = ExperimentRunner(workspace=self.workspace)

        # Use sandbox runner if configured
        if config.sandbox and config.sandbox.backend != "none":
            from crucible.sandbox import SandboxRunner
            self.runner = SandboxRunner(
                config=config.sandbox,
                workspace=self.workspace,
                editable_files=config.files.editable,
            )

        self.context = ContextAssembler(
            config=config,
            project_root=self.workspace,
            branch_name=branch_name,
        )

        self.budget = BudgetGuard(config.constraints.budget)

        # Allow agent to install packages via requirements.txt
        if config.constraints.allow_install:
            self.guardrails.editable.add("requirements.txt")
            req = self.workspace / "requirements.txt"
            if not req.exists():
                req.touch()

        self._fail_seq = 0
        self._consecutive_failures = 0
        self._consecutive_skips = 0
        self._stop = False
        self._iteration = 0

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
            self.results.seed_baseline(metric_value, commit[:7], source_tag)
        # Ensure generated files are gitignored so reset doesn't revert them
        # and agents don't trigger violations by accidentally touching them
        gitignore = self.workspace / ".gitignore"
        lines = gitignore.read_text().splitlines() if gitignore.exists() else []
        needed = [p for p in ("results-*.jsonl", "run.log", "logs/") if p not in lines]
        if needed:
            lines.extend(needed)
            gitignore.write_text("\n".join(lines) + "\n")
            self.git.commit("chore: gitignore generated files")

    def resume(self) -> None:
        """Resume an existing experiment branch."""
        self.git.checkout_branch(self.tag)
        existing = self.results.read_all()
        self._fail_seq = sum(1 for r in existing if r.status in ("crash", "discard"))
        self._iteration = len(existing)

    def run_one_iteration(self) -> str:
        """Execute one full experiment cycle.

        Returns a status string: "keep", "discard", "crash", "violation", or "skip".
        """
        self._iteration += 1

        # 1. Assemble prompt (inline files for agents without read capability)
        if "read" not in self.agent.capabilities():
            prompt = self.context.assemble_with_files(
                self.results, self.workspace, self.config.files.editable,
            )
        else:
            prompt = self.context.assemble(self.results)

        # 2. Call agent (hidden files are protected via SDK can_use_tool callback)
        t0_agent = time.monotonic()
        agent_result = self.agent.generate_edit(prompt, self.workspace)
        agent_duration = time.monotonic() - t0_agent

        # Budget check
        self.budget.accumulate(agent_result.usage)
        verdict = self.budget.check(agent_result.usage)
        if verdict == "exceeded":
            logger.warning("Budget exceeded — stopping")
            return "budget_exceeded"
        elif verdict == "warning":
            logger.warning(f"Budget at {self.budget.percent_used:.0f}%")

        # 3. Strip hidden files from modified list (agent may have created them on disk)
        hidden_set = set(self.config.files.hidden)
        modified = [str(p) for p in agent_result.modified_files if str(p) not in hidden_set]

        # 4. Check edits via guard rails
        violation = self.guardrails.check_edits(modified)

        # 5. Handle violation
        #    Violations and skips don't count toward consecutive failures —
        #    no real experiment ran, so the agent just needs better guidance.
        #    Only crash and discard (real failed experiments) trigger stopping.
        if violation is not None:
            if violation.kind == "no_edits":
                self.context.requeue_crash_info()
                return "skip"
            self.git.revert_changes()
            self.context.add_error(violation.message)
            return "violation"

        # 7. Git commit
        self.git.commit(agent_result.description)
        commit_hash = self.git.head()

        # Install updated requirements if allow_install is enabled
        # Docker mode: skip pip install here — _hash_deps() will detect the change
        # and rebuild the image with deps baked in (build has network access)
        if (self.config.constraints.allow_install
                and "requirements.txt" in modified
                and not (self.config.sandbox and self.config.sandbox.backend != "none")):
            self._install_requirements()

        # Compute delta from current best
        current_best = self.results.best(self.config.metric.direction)
        best_val = current_best.metric_value if current_best else None

        # 8. Execute experiment (with optional repeat)
        t0_run = time.monotonic()
        eval_cfg = self.config.evaluation
        if eval_cfg.repeat > 1:
            run_result, metric_value = self.runner.execute_with_repeat(
                self.config.commands.run, self.config.commands.eval,
                self.config.metric.name, eval_cfg.repeat,
                eval_cfg.aggregation, self.config.constraints.timeout_seconds,
            )
        else:
            run_result = self.runner.execute(
                self.config.commands.run,
                self.config.constraints.timeout_seconds,
            )
            metric_value = None
            if run_result.exit_code == 0 and not run_result.timed_out:
                metric_value = self.runner.parse_metric(
                    self.config.commands.eval,
                    self.config.metric.name,
                )
        run_duration = time.monotonic() - t0_run

        total_duration = agent_duration + run_duration

        # Save per-iteration logs (agent reasoning + run.log)
        self._save_iteration_logs(self._iteration, agent_result)

        # 10. Handle crash (metric is None or invalid)
        if metric_value is None or not self.guardrails.check_metric(metric_value):
            self._fail_seq += 1
            self.git.tag_failed_and_reset(self.tag, self._fail_seq)
            self.results.log(self._make_record(
                "crash", 0.0, agent_result.description,
                commit_hash, agent_result, total_duration,
            ))
            crash_msg = run_result.stderr_tail
            if run_result.timed_out:
                crash_msg = (
                    f"TIMED OUT after {self.config.constraints.timeout_seconds}s. "
                    "Your changes made the code too slow. Reduce model size, "
                    "training epochs, or MCTS simulations.\n" + crash_msg
                )
            self.context.add_crash_info(crash_msg)
            self._consecutive_failures += 1
            return "crash"

        # Compute delta
        delta = (metric_value - best_val) if best_val is not None else None
        delta_percent = (
            (delta / abs(best_val) * 100) if delta is not None and best_val != 0 else None
        )

        # 11. Check improvement
        if self.results.is_improvement(metric_value, self.config.metric.direction):
            self.results.log(self._make_record(
                "keep", metric_value, agent_result.description,
                commit_hash, agent_result, total_duration,
                delta=delta, delta_percent=delta_percent,
            ))
            self._consecutive_failures = 0
            return "keep"

        # 12. Discard
        self._fail_seq += 1
        self.git.tag_failed_and_reset(self.tag, self._fail_seq)
        self.results.log(self._make_record(
            "discard", metric_value, agent_result.description,
            commit_hash, agent_result, total_duration,
            delta=delta, delta_percent=delta_percent,
        ))
        self._consecutive_failures += 1
        return "discard"

    def _make_record(
        self,
        status: str,
        metric_value: float,
        description: str,
        commit: str,
        agent_result,
        duration_seconds: float,
        delta: float | None = None,
        delta_percent: float | None = None,
    ) -> ExperimentRecord:
        """Build an ExperimentRecord with common fields filled in."""
        return ExperimentRecord(
            commit=commit,
            metric_value=metric_value,
            status=status,
            description=description,
            iteration=self._iteration,
            timestamp=datetime.now(timezone.utc).isoformat(),
            delta=delta,
            delta_percent=delta_percent,
            files_changed=[str(f) for f in agent_result.modified_files],
            diff_stats=self._get_diff_stats(commit),
            duration_seconds=duration_seconds,
            usage=agent_result.usage,
            log_dir=f"logs/iter-{self._iteration}",
        )

    def _save_iteration_logs(self, iteration: int, agent_result) -> None:
        """Save agent output and run.log to logs/iter-{N}/."""
        log_dir = self.workspace / "logs" / f"iter-{iteration}"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Save agent reasoning
        if agent_result.agent_output:
            (log_dir / "agent.txt").write_text(agent_result.agent_output)

        # Copy run.log if exists
        run_log = self.workspace / "run.log"
        if run_log.exists():
            shutil.copy2(run_log, log_dir / "run.log")

    def _install_requirements(self):
        """Install packages from requirements.txt."""
        req = self.workspace / "requirements.txt"
        if not req.exists():
            return
        logger.info("Installing updated requirements...")
        result = subprocess.run(
            ["pip", "install", "-r", str(req)],
            cwd=self.workspace,
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.warning(f"pip install failed: {result.stderr[-200:]}")

    def _get_diff_stats(self, commit: str) -> dict:
        """Get insertion/deletion counts for a commit."""
        result = subprocess.run(
            ["git", "diff", "--numstat", f"{commit}~1", commit],
            cwd=self.workspace, capture_output=True, text=True,
        )
        insertions = deletions = 0
        for line in result.stdout.strip().splitlines():
            parts = line.split('\t')
            if len(parts) >= 2:
                try:
                    insertions += int(parts[0])
                    deletions += int(parts[1])
                except ValueError:
                    pass
        return {"insertions": insertions, "deletions": deletions}

    def run_loop(self) -> None:
        """Run iterations indefinitely until Ctrl+C."""
        max_retries = self.config.constraints.max_retries
        try:
            while True:
                logger.info(f"--- iter {self._iteration + 1} ---")
                status = self.run_one_iteration()

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
