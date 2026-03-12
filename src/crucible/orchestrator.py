"""Orchestrator — core experiment loop tying all modules together."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from crucible.agents.base import AgentInterface
from crucible.config import Config
from crucible.context import ContextAssembler
from crucible.git_manager import GitManager
from crucible.guardrails import GuardRails
from crucible.results import ResultsLog, results_filename
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
        self.context = ContextAssembler(
            config=config,
            project_root=self.workspace,
            branch_name=branch_name,
        )

        self._fail_seq = 0
        self._consecutive_failures = 0
        self._consecutive_skips = 0
        self._stop = False

    def init(self) -> None:
        """Create the experiment branch and initialise results-{tag}.tsv."""
        self.git.create_branch(self.tag)
        self.results.init()
        # Ensure generated files are gitignored so reset doesn't revert them
        # and agents don't trigger violations by accidentally touching them
        gitignore = self.workspace / ".gitignore"
        lines = gitignore.read_text().splitlines() if gitignore.exists() else []
        needed = [p for p in ("results-*.tsv", "run.log") if p not in lines]
        if needed:
            lines.extend(needed)
            gitignore.write_text("\n".join(lines) + "\n")
            self.git.commit("chore: gitignore generated files")

    def resume(self) -> None:
        """Resume an existing experiment branch."""
        self.git.checkout_branch(self.tag)
        existing = self.results.read_all()
        self._fail_seq = sum(1 for r in existing if r.status in ("crash", "discard"))

    def run_one_iteration(self) -> str:
        """Execute one full experiment cycle.

        Returns a status string: "keep", "discard", "crash", "violation", or "skip".
        """
        # 1. Assemble prompt
        prompt = self.context.assemble(self.results)

        # 2. Call agent (hidden files are protected via SDK can_use_tool callback)
        agent_result = self.agent.generate_edit(prompt, self.workspace)

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

        # 8. Execute experiment
        run_result = self.runner.execute(
            self.config.commands.run,
            self.config.constraints.timeout_seconds,
        )

        # 9. Parse metric
        metric_value: Optional[float] = None
        if run_result.exit_code == 0 and not run_result.timed_out:
            metric_value = self.runner.parse_metric(
                self.config.commands.eval,
                self.config.metric.name,
            )

        # 10. Handle crash (metric is None or invalid)
        if metric_value is None or not self.guardrails.check_metric(metric_value):
            self._fail_seq += 1
            self.git.tag_failed_and_reset(self.tag, self._fail_seq)
            self.results.log(
                commit=commit_hash,
                metric_value=0.0,
                status="crash",
                description=agent_result.description,
            )
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

        # 11. Check improvement
        if self.results.is_improvement(metric_value, self.config.metric.direction):
            self.results.log(
                commit=commit_hash,
                metric_value=metric_value,
                status="keep",
                description=agent_result.description,
            )
            self._consecutive_failures = 0
            return "keep"

        # 12. Discard
        self._fail_seq += 1
        self.git.tag_failed_and_reset(self.tag, self._fail_seq)
        self.results.log(
            commit=commit_hash,
            metric_value=metric_value,
            status="discard",
            description=agent_result.description,
        )
        self._consecutive_failures += 1
        return "discard"

    def run_loop(self) -> None:
        """Run iterations indefinitely until Ctrl+C."""
        iteration = 0
        max_retries = self.config.constraints.max_retries
        try:
            while True:
                iteration += 1
                logger.info(f"--- iter {iteration} ---")
                status = self.run_one_iteration()

                best = self.results.best(self.config.metric.direction)
                best_str = f"{best.metric_value}" if best else "N/A"
                logger.info(f"[iter {iteration}] {status} | best {self.config.metric.name}: {best_str}")

                if status in ("skip", "violation"):
                    self._consecutive_skips += 1
                else:
                    self._consecutive_skips = 0

                if self._consecutive_failures >= max_retries:
                    logger.warning(f"[iter {iteration}] {max_retries} consecutive failures, stopping.")
                    break
                if self._consecutive_skips >= max_retries:
                    logger.warning(f"[iter {iteration}] {max_retries} consecutive skips, stopping.")
                    break
        except KeyboardInterrupt:
            logger.info(f"Stopped after {iteration} iterations.")
