"""Orchestrator — core experiment loop tying all modules together."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from crucible.agents.base import AgentInterface
from crucible.config import Config
from crucible.context import ContextAssembler
from crucible.git_manager import GitManager
from crucible.guardrails import GuardRails
from crucible.results import ResultsLog
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
        self.results = ResultsLog(self.workspace / "results.tsv")
        self.runner = ExperimentRunner(workspace=self.workspace)
        self.context = ContextAssembler(
            config=config,
            project_root=self.workspace,
            branch_name=branch_name,
        )

        self._fail_seq = 0
        self._consecutive_failures = 0
        self._stop = False

    def init(self) -> None:
        """Create the experiment branch and initialise results.tsv."""
        self.git.create_branch(self.tag)
        self.results.init()
        # Ensure results.tsv is gitignored so reset doesn't revert it
        gitignore = self.workspace / ".gitignore"
        lines = gitignore.read_text().splitlines() if gitignore.exists() else []
        if "results.tsv" not in lines:
            lines.append("results.tsv")
            gitignore.write_text("\n".join(lines) + "\n")
            # Commit .gitignore so it's clean before the experiment loop
            self.git.commit("chore: update .gitignore with results.tsv")

    def run_one_iteration(self) -> str:
        """Execute one full experiment cycle.

        Returns a status string: "keep", "discard", "crash", "violation", or "skip".
        """
        # 1. Assemble prompt
        prompt = self.context.assemble(self.results)

        # 2. Call agent
        agent_result = self.agent.generate_edit(prompt, self.workspace)

        # 3. Check edits via guard rails
        modified = [str(p) for p in agent_result.modified_files]
        violation = self.guardrails.check_edits(modified)

        # 4. Handle violation
        if violation is not None:
            if violation.kind == "no_edits":
                return "skip"
            self.git.revert_changes()
            self.context.add_error(violation.message)
            self._consecutive_failures += 1
            return "violation"

        # 5. Git commit
        self.git.commit(agent_result.description)
        commit_hash = self.git.head()

        # 6. Execute experiment
        run_result = self.runner.execute(
            self.config.commands.run,
            self.config.constraints.timeout_seconds,
        )

        # 7. Parse metric
        metric_value: Optional[float] = None
        if run_result.exit_code == 0 and not run_result.timed_out:
            metric_value = self.runner.parse_metric(
                self.config.commands.eval,
                self.config.metric.name,
            )

        # 8. Handle crash (metric is None or invalid)
        if metric_value is None or not self.guardrails.check_metric(metric_value):
            self._fail_seq += 1
            self.git.tag_failed_and_reset(self.tag, self._fail_seq)
            self.results.log(
                commit=commit_hash,
                metric_value=0.0,
                status="crash",
                description=agent_result.description,
            )
            self.context.add_crash_info(run_result.stderr_tail)
            self._consecutive_failures += 1
            return "crash"

        # 9. Check improvement
        if self.results.is_improvement(metric_value, self.config.metric.direction):
            self.results.log(
                commit=commit_hash,
                metric_value=metric_value,
                status="keep",
                description=agent_result.description,
            )
            self._consecutive_failures = 0
            return "keep"

        # 10. Discard
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
                status = self.run_one_iteration()

                best = self.results.best(self.config.metric.direction)
                best_str = f"{best.metric_value}" if best else "N/A"
                print(f"[iter {iteration}] {status} | best {self.config.metric.name}: {best_str}")
                sys.stdout.flush()

                if self._consecutive_failures >= max_retries:
                    print(f"[iter {iteration}] {max_retries} consecutive failures, stopping.")
                    break
        except KeyboardInterrupt:
            print(f"\nStopped after {iteration} iterations.")
