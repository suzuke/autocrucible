"""Orchestrator — core experiment loop tying all modules together."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from dataclasses import dataclass, field

from crucible.agents.base import AgentErrorType, AgentInterface
from crucible.budget import BudgetGuard
from crucible.config import Config
from crucible.i18n import _
from crucible.context import ContextAssembler
from crucible.git_manager import GitManager
from crucible.guardrails import GuardRails
from crucible.ledger import (
    DIFF_TEXT_INLINE_LIMIT_BYTES,
    AttemptNode,
    TrialLedger,
)
from crucible.results import ExperimentRecord, ResultsLog, results_filename
from crucible.runner import ExperimentRunner
from crucible.strategy import (
    BranchFrom,
    Continue as StrategyContinue,
    Restart as StrategyRestart,
    SearchStrategy,
    Stop as StrategyStop,
    StrategyContext,
    make_strategy,
)


@dataclass
class BeamState:
    """Per-beam state for beam search strategy."""
    beam_id: int
    results: ResultsLog
    context: ContextAssembler
    consecutive_failures: int = 0
    consecutive_skips: int = 0
    fail_seq: int = 0
    iteration: int = 0


_FATAL_MSG = _("Fatal error — cannot continue. Check: claude login")


class Orchestrator:
    """Ties all modules together into the core experiment loop."""

    def __init__(
        self,
        config: Config,
        workspace: Path | str,
        tag: str,
        agent: AgentInterface,
        *,
        profile: bool = False,
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
            hidden=config.files.hidden,
            workspace=self.workspace,  # M1b: enable SSOT mode (symlink/hardlink defenses)
        )
        self.results = ResultsLog(self.workspace / results_filename(tag))
        # M1a: dual-write to TrialLedger alongside ResultsLog. Storage is
        # purely additive; ResultsLog format unchanged for backward compat.
        self.ledger = TrialLedger(
            self.workspace / "logs" / f"run-{tag}" / "ledger.jsonl"
        )
        self.runner = ExperimentRunner(workspace=self.workspace)

        # Use sandbox runner if configured
        if config.sandbox and config.sandbox.backend != "none":
            from crucible.sandbox import SandboxRunner
            self.runner = SandboxRunner(
                config=config.sandbox,
                workspace=self.workspace,
                editable_files=config.files.editable,
                artifact_dirs=config.files.artifacts,
            )

        self.context = ContextAssembler(
            config=config,
            project_root=self.workspace,
            branch_name=branch_name,
        )

        self.budget = BudgetGuard(config.constraints.budget)

        # Allow agent to install packages via requirements.txt
        if config.constraints.allow_install:
            self.guardrails.add_editable("requirements.txt")
            req = self.workspace / "requirements.txt"
            if not req.exists():
                req.touch()

        self._fail_seq = 0
        self._consecutive_failures = 0
        self._consecutive_skips = 0
        self._stop = False
        self._iteration = 0
        self._baseline_commit: str = ""
        self._current_beam_id: int | None = None
        self._beams: list[BeamState] = []
        self._current_beam_idx: int = 0
        self._profile = profile
        # M1a: parent_id tracking for AttemptNode tree edges. Linear strategies
        # (greedy / restart) → key=None. Beam strategy → key=beam_id.
        self._last_attempt_id_by_beam: dict[Optional[int], Optional[str]] = {}
        # M1b PR 2: SearchStrategy Protocol instance. Beam still uses the
        # legacy _run_loop_beam path; "greedy" / "restart" / "bfts-lite" go
        # through self.strategy.decide() in _run_loop_serial.
        try:
            if config.search.strategy != "beam":
                self.strategy: SearchStrategy | None = make_strategy(
                    config.search.strategy,
                    prune_threshold=config.search.prune_threshold,
                )
            else:
                self.strategy = None
        except ValueError:
            # Unknown strategy name → fall back to legacy string-branching path.
            logger.warning("unknown search strategy %r — using legacy path",
                           config.search.strategy)
            self.strategy = None
        # M2 PR 12: validate seal config at startup so HMAC misconfig
        # fails immediately rather than after the first eval finishes.
        # Cheap no-op for the default content-sha256 algorithm.
        from crucible.sealing import validate_seal_config
        validate_seal_config(config.seal)

        self._critic = None
        if config.agent.critic.enabled:
            from crucible.agents.critic import CriticAgent
            self._critic = CriticAgent(model=config.agent.critic.model)

        # M3: Warn if convergence_window <= plateau_threshold in restart mode
        cw = config.constraints.convergence_window
        pt = config.search.plateau_threshold
        if (cw is not None
                and config.search.strategy == "restart"
                and cw <= pt):
            logger.warning(
                "convergence_window (%d) <= plateau_threshold (%d): "
                "experiment will stop before restart can take effect",
                cw, pt,
            )

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
        self._baseline_commit = self.git.head()
        self.results.init()
        if fork_from is not None:
            self.results.seed_baseline(metric_value, commit[:7], source_tag)
        # Ensure generated files are gitignored so reset doesn't revert them
        # and agents don't trigger violations by accidentally touching them
        gitignore = self.workspace / ".gitignore"
        lines = gitignore.read_text().splitlines() if gitignore.exists() else []
        needed = [p for p in ("results-*.jsonl", "run.log", "logs/", ".crucible/.validated") if p not in lines]
        # Add artifacts paths to gitignore and create directories
        for artifact_path in self.config.files.artifacts:
            if artifact_path not in lines:
                needed.append(artifact_path)
            (self.workspace / artifact_path).mkdir(parents=True, exist_ok=True)
        if needed:
            lines.extend(needed)
            gitignore.write_text("\n".join(lines) + "\n")
            self.git.commit("chore: gitignore generated files")

    def resume(self) -> None:
        """Resume an existing experiment branch.

        Reads BOTH ResultsLog and TrialLedger to set _iteration and rebuild
        the parent_id chain. Required because violation/skip outcomes are
        recorded in the ledger ONLY (PR 2.1) — if we read only ResultsLog,
        we'd undercount _iteration and create duplicate AttemptNode IDs.
        """
        self.git.checkout_branch(self.tag)
        existing = self.results.read_all()
        self._fail_seq = sum(1 for r in existing if r.status in ("crash", "discard"))

        # Source 1: ResultsLog (counts keep/discard/crash)
        results_max = max((r.iteration for r in existing if r.iteration is not None),
                          default=0)
        # Source 2: TrialLedger (counts everything including violation/skip)
        ledger_max = 0
        try:
            ledger_nodes = self.ledger.all_nodes()
        except Exception:
            ledger_nodes = []
        if ledger_nodes:
            # Parse the trailing numeric portion from "n000042" or "b1n000042"
            for n in ledger_nodes:
                tail = n.id.split("n")[-1] if "n" in n.id else "0"
                try:
                    ledger_max = max(ledger_max, int(tail))
                except ValueError:
                    pass

        self._iteration = max(len(existing), results_max, ledger_max)

        # Rebuild parent chain from ledger so the next AttemptNode links
        # correctly. Walk in append order and remember the last KEPT id
        # seen per beam (None for linear). Only kept attempts carry code
        # commits; discard/crash/violation/skip nodes appear in the ledger
        # but do not advance the code-parent pointer (M1b PR 8a semantics).
        self._last_attempt_id_by_beam = {}
        for n in ledger_nodes:
            if n.outcome != "keep":
                continue
            beam: int | None = None
            if n.id.startswith("b") and "n" in n.id:
                try:
                    beam = int(n.id[1 : n.id.index("n")])
                except ValueError:
                    beam = None
            self._last_attempt_id_by_beam[beam] = n.id

    def run_one_iteration(self) -> str:
        """Execute one full experiment cycle.

        Returns a status string: "keep", "discard", "crash", "violation",
        "skip", "budget_exceeded", or "fatal".
        """
        self._iteration += 1

        # 0. Run critic analysis (Plan B) if enabled
        if self._critic and self._iteration > 1:
            records = self.results.read_all()
            agent_log = self._read_last_agent_log()
            critic_notes = self._critic.analyze(
                records, self.workspace, self._iteration, agent_log,
            )
            if critic_notes:
                logger.info("[critic] %s", critic_notes.split("\n")[0])
                self.context.set_critic_notes(critic_notes)

        # 1. Assemble prompt (inline files for agents without read capability)
        if "read" not in self.agent.capabilities():
            prompt = self.context.assemble_with_files(
                self.results, self.workspace, self.config.files.editable,
                profile=self._profile,
            )
        else:
            prompt = self.context.assemble(self.results, profile=self._profile)

        # 2. Call agent (hidden files are protected via SDK can_use_tool callback)
        t0_agent = time.monotonic()
        agent_result = self.agent.generate_edit(prompt, self.workspace)
        agent_duration = time.monotonic() - t0_agent

        # M1a: persist prompt.md early so violation/skip paths (which early-return
        # before _save_iteration_logs) still leave a trace for AttemptNode.prompt_ref.
        try:
            log_dir = self.workspace / "logs" / f"iter-{self._iteration}"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "prompt.md").write_text(prompt)
        except OSError as exc:  # never break the loop on disk errors
            logger.warning("could not save prompt.md: %s", exc)

        # Budget check
        self.budget.accumulate(agent_result.usage)
        verdict = self.budget.check(agent_result.usage)
        if verdict == "exceeded":
            logger.warning(_("Budget exceeded — stopping"))
            return "budget_exceeded"
        elif verdict == "warning":
            logger.warning(_("Budget at {pct:.0f}%").format(pct=self.budget.percent_used))

        # Fatal error — unrecoverable, abort immediately
        if agent_result.error_type == AgentErrorType.AUTH:
            logger.error(
                f"[iter {self._iteration}] Authentication error: "
                f"{agent_result.description}"
            )
            return "fatal"

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
                self._ledger_log_no_commit("skip", agent_result, violation.message)
                return "skip"
            self.git.revert_changes()
            self.context.add_error(violation.message)
            self._ledger_log_no_commit("violation", agent_result, violation.message)
            return "violation"

        # 7. Git commit
        self.git.commit(agent_result.description)
        commit_hash = self.git.head()
        diff_text = self.git.compact_diff(commit_hash)

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
        metric_parse_result = None  # M1b PR 8b: only the non-repeat path
                                    # populates this; repeat path uses its
                                    # own aggregated metric.
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
                # Prefer parse_metric_result (M1b PR 8b) for full eval streams.
                # Fall back to legacy parse_metric for runners that haven't
                # been upgraded yet (e.g., custom backends or test mocks).
                if hasattr(self.runner, "parse_metric_result"):
                    metric_parse_result = self.runner.parse_metric_result(
                        self.config.commands.eval,
                        self.config.metric.name,
                        timeout=self.config.constraints.timeout_seconds,
                    )
                    metric_value = metric_parse_result.metric_value
                else:
                    metric_value = self.runner.parse_metric(
                        self.config.commands.eval,
                        self.config.metric.name,
                        timeout=self.config.constraints.timeout_seconds,
                    )
        run_duration = time.monotonic() - t0_run

        total_duration = agent_duration + run_duration

        # Save per-iteration logs (agent reasoning + run.log + prompt + diff)
        self._save_iteration_logs(
            self._iteration, agent_result, prompt=prompt, diff_text=diff_text,
        )

        # M1b PR 6 + 8b: write sealed EvalResult artefact for this iteration.
        # PR 8b (reviewer F1): hash the EVAL command's stdout/stderr (not
        # the run command's), so the integrity hash actually proves the
        # bytes that produced metric_value.
        if metric_parse_result is not None:
            seal_stdout = metric_parse_result.stdout
            seal_stderr_tail = metric_parse_result.stderr_tail
            seal_exit = metric_parse_result.exit_code
            seal_timed_out = metric_parse_result.timed_out
        else:
            # Crashed before eval ran — seal empty bytes for the eval, but
            # keep the run's exit_code so the artefact reflects what happened.
            seal_stdout = ""
            seal_stderr_tail = run_result.stderr_tail or ""
            seal_exit = run_result.exit_code
            seal_timed_out = run_result.timed_out

        eval_result_ref, eval_result_sha256 = self._write_eval_result_artifact(
            iteration=self._iteration,
            commit_hash=commit_hash,
            seal_stdout=seal_stdout,
            seal_stderr_tail=seal_stderr_tail,
            seal_exit_code=seal_exit,
            seal_timed_out=seal_timed_out,
            metric_value=metric_value,
            run_duration_seconds=run_duration,
        )
        self._pending_eval_result_ref = eval_result_ref
        self._pending_eval_result_sha256 = eval_result_sha256

        # 10. Handle crash (metric is None or invalid)
        if metric_value is None or not self.guardrails.check_metric(metric_value):
            self._fail_seq += 1
            self.git.tag_failed_and_reset(self.tag, self._fail_seq)
            self._dual_log(self._make_record(
                "crash", 0.0, agent_result.description,
                commit_hash, agent_result, total_duration,
                agent_duration_seconds=agent_duration,
                run_duration_seconds=run_duration,
                diff_text=diff_text,
            ), agent_result, diff_text=diff_text)
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
            self._dual_log(self._make_record(
                "keep", metric_value, agent_result.description,
                commit_hash, agent_result, total_duration,
                delta=delta, delta_percent=delta_percent,
                agent_duration_seconds=agent_duration,
                run_duration_seconds=run_duration,
                diff_text=diff_text,
            ), agent_result, diff_text=diff_text)
            self._consecutive_failures = 0
            return "keep"

        # 12. Discard
        self._fail_seq += 1
        self.git.tag_failed_and_reset(self.tag, self._fail_seq)
        self._dual_log(self._make_record(
            "discard", metric_value, agent_result.description,
            commit_hash, agent_result, total_duration,
            delta=delta, delta_percent=delta_percent,
            agent_duration_seconds=agent_duration,
            run_duration_seconds=run_duration,
            diff_text=diff_text,
        ), agent_result, diff_text=diff_text)
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
        agent_duration_seconds: float | None = None,
        run_duration_seconds: float | None = None,
        diff_text: str | None = None,
    ) -> ExperimentRecord:
        """Build an ExperimentRecord with common fields filled in."""
        usage = agent_result.usage
        # Copy prompt_breakdown into usage without mutating agent_result
        if self._profile and usage and self.context.prompt_breakdown:
            from dataclasses import replace
            usage = replace(usage, prompt_breakdown=self.context.prompt_breakdown)

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
            diff_text=diff_text,
            duration_seconds=duration_seconds,
            agent_duration_seconds=agent_duration_seconds,
            run_duration_seconds=run_duration_seconds,
            usage=usage,
            log_dir=f"logs/iter-{self._iteration}",
            beam_id=self._current_beam_id,
        )

    def _write_eval_result_artifact(
        self,
        iteration: int,
        commit_hash: str,
        seal_stdout: str,
        seal_stderr_tail: str,
        seal_exit_code: int,
        seal_timed_out: bool,
        metric_value: float | None,
        run_duration_seconds: float,
    ) -> tuple[str, str] | tuple[None, None]:
        """Write a sealed EvalResult JSON artefact for this iteration.

        Returns (relative_path, sha256_hash) on success, or (None, None) on
        failure (does not raise — never breaks the experiment loop).

        Per spec §4 / §11: the host process is the SOLE writer; agent
        code never produces this file. The integrity hash is the sha256
        of canonical JSON (M1) — M2 will upgrade to HMAC-SHA256 once
        secret-key management lands.

        PR 8b (reviewer F1): seal_stdout / seal_stderr_tail come from the
        EVAL command (not run_cmd), so the hash actually proves the
        bytes that produced metric_value. seal_stderr_tail is the tail
        only — RunResult does not carry full stderr — so this is named
        stderr_TAIL_sha256 honestly.
        """
        import hashlib
        import json as _json
        from datetime import datetime, timezone

        from crucible.ledger import LEDGER_SCHEMA_VERSION, EvalResult

        try:
            beam = self._current_beam_id
            attempt_id = (
                AttemptNode.short_id(iteration)
                if beam is None
                else f"b{beam}{AttemptNode.short_id(iteration)}"
            )

            # Manifest hash: combines eval_command, run_command, metric_name.
            # M2 may include entry_file_hash + config_hash for stronger
            # tampering detection.
            manifest_payload = (
                f"{self.config.commands.eval}|"
                f"{self.config.commands.run}|"
                f"{self.config.metric.name}"
            )
            manifest_hash = hashlib.sha256(
                manifest_payload.encode("utf-8")
            ).hexdigest()

            stdout_hash = hashlib.sha256(
                (seal_stdout or "").encode("utf-8")
            ).hexdigest()
            stderr_hash = hashlib.sha256(
                (seal_stderr_tail or "").encode("utf-8")
            ).hexdigest()

            payload = EvalResult(
                schema_version=LEDGER_SCHEMA_VERSION,
                run_id=self.tag,
                attempt_id=attempt_id,
                commit=commit_hash or "",
                eval_command=self.config.commands.eval,
                eval_manifest_hash=manifest_hash,
                metric_name=self.config.metric.name,
                metric_value=metric_value,
                metric_direction=self.config.metric.direction,
                diagnostics={},  # M2 will best-effort parse from stdout
                valid=metric_value is not None,
                exit_code=seal_exit_code,
                timed_out=seal_timed_out,
                duration_ms=int(run_duration_seconds * 1000),
                stdout_sha256=stdout_hash,
                stderr_sha256=stderr_hash,
                seal=None,  # populated below
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            # Canonical JSON for sealing (sorted keys, no whitespace variability).
            from dataclasses import asdict
            payload_dict = asdict(payload)
            canonical = _json.dumps(
                payload_dict, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            from crucible.sealing import compute_seal
            payload.seal = compute_seal(canonical, config=self.config.seal)
            payload_dict["seal"] = payload.seal

            # Write under logs/run-<tag>/iter-<N>/eval-result.json (per spec §4).
            # Co-located with prompt.md and diff.patch so HTML reporter can
            # cross-reference.
            target_dir = self.workspace / "logs" / f"run-{self.tag}" / f"iter-{iteration}"
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / "eval-result.json"
            target_path.write_text(_json.dumps(payload_dict, indent=2))

            # Compute sha256 of the bytes actually on disk (may differ from
            # canonical_json if json.dumps with indent=2 reorders / spaces).
            disk_hash = hashlib.sha256(target_path.read_bytes()).hexdigest()

            rel_path = target_path.relative_to(self.workspace).as_posix()
            return rel_path, disk_hash
        except Exception as exc:
            logger.warning("could not write eval-result.json: %s", exc)
            return None, None

    def _lookup_commit_for_node(self, node_id: str) -> str | None:
        """Resolve an AttemptNode id back to its commit sha by reading the
        ledger. Returns None if not found or commit is empty (e.g., a
        violation/skip node has no commit)."""
        try:
            for node in self.ledger.all_nodes():
                if node.id == node_id and node.commit:
                    return node.commit
        except Exception as exc:
            logger.warning("ledger lookup failed for %s: %s", node_id, exc)
        return None

    def _log_strategy_decision(
        self,
        sctx: StrategyContext,
        action,
    ) -> None:
        """M3 PR 17: append one decision record to the sidecar.

        Sidecar lives at `logs/run-<tag>/strategy-decisions.jsonl`.
        Best-effort: any failure (FS error, serialization issue) is
        swallowed so a logging hiccup never blocks the run loop.
        """
        try:
            from crucible.strategy_decisions import StrategyDecision, append

            run_dir = self.workspace / "logs" / f"run-{self.tag}"
            kept = [
                n.id for n in sctx.ledger_nodes
                if n.outcome == "keep" and n.id in sctx.metric_lookup
            ]
            # Best-effort: ask the strategy which kept candidates it
            # would prune. BFTSLiteStrategy implements `should_prune`;
            # other strategies' default is False so the pruned list
            # ends up empty for them.
            pruned = []
            should_prune = getattr(self.strategy, "should_prune", None)
            if callable(should_prune):
                pruned = [
                    nid for nid in kept
                    if should_prune(sctx, nid)
                ]
            chosen = type(action).__name__ if action is not None else "None"
            rationale = getattr(action, "reason", "") or ""
            append(
                run_dir,
                StrategyDecision(
                    timestamp=StrategyDecision.now_iso(),
                    iteration=sctx.iteration_count,
                    kept_candidates=kept,
                    pruned_candidates=pruned,
                    chosen_action=chosen,
                    rationale=rationale,
                    extras={
                        "strategy": getattr(self.strategy, "name", ""),
                        "plateau_streak": sctx.plateau_streak,
                    },
                ),
            )
        except Exception as exc:
            logger.debug("strategy decision log failed: %s", exc)

    def _build_strategy_context(
        self,
        session_count: int,
        plateau_threshold: int,
        max_iterations: int | None,
    ) -> StrategyContext:
        """Snapshot the orchestrator state for SearchStrategy.decide().

        Reads ledger + ResultsLog so strategies (notably BFTSLite) have
        the full attempt-tree view. metric_lookup is built from the
        committed ResultsLog records keyed by AttemptNode id scheme.
        """
        try:
            ledger_nodes = list(self.ledger.all_nodes())
        except Exception:
            ledger_nodes = []

        # Map iteration → metric_value via ResultsLog (keep records only;
        # discards/crashes have no useful metric for "best").
        metric_lookup: dict[str, float] = {}
        try:
            for r in self.results.read_all():
                if r.metric_value is None or r.iteration is None:
                    continue
                if r.beam_id is None:
                    attempt_id = AttemptNode.short_id(r.iteration)
                else:
                    attempt_id = f"b{r.beam_id}{AttemptNode.short_id(r.iteration)}"
                metric_lookup[attempt_id] = float(r.metric_value)
        except Exception:
            pass

        streak = self._count_plateau_streak()
        return StrategyContext(
            ledger_nodes=tuple(ledger_nodes),
            metric_lookup=metric_lookup,
            metric_direction=self.config.metric.direction,
            iteration_count=session_count,
            plateau_streak=streak,
            plateau_threshold=plateau_threshold,
            max_iterations=max_iterations,
            baseline_commit=self._baseline_commit,
        )

    def _record_to_attempt_node(
        self,
        record: ExperimentRecord,
        agent_result,
        diff_text: str | None,
    ) -> AttemptNode:
        """Translate an ExperimentRecord into an AttemptNode for ledger append.

        Mapping is intentionally lossy: ResultsLog stays the source of truth
        for metric/usage/diff_stats; AttemptNode is a normalised tree-edge
        index. Eval result fields (eval_result_ref / sha256) are populated
        in M1b when sealed EvalResult artefacts land.
        """
        beam = record.beam_id
        seq = record.iteration if record.iteration is not None else 0
        if beam is None:
            attempt_id = AttemptNode.short_id(seq)
        else:
            attempt_id = f"b{beam}{AttemptNode.short_id(seq)}"
        parent_id = self._last_attempt_id_by_beam.get(beam)

        # Cap inline diff at the ledger's hard limit, full content via diff_ref.
        diff_inline = ""
        if diff_text:
            encoded = diff_text.encode("utf-8")
            if len(encoded) > DIFF_TEXT_INLINE_LIMIT_BYTES:
                # Reserve room for the truncation marker.
                head = encoded[: DIFF_TEXT_INLINE_LIMIT_BYTES - 64]
                diff_inline = head.decode("utf-8", errors="ignore") + "\n... [TRUNCATED]"
            else:
                diff_inline = diff_text

        diff_ref = f"{record.log_dir}/diff.patch" if record.log_dir else ""
        prompt_ref = f"{record.log_dir}/prompt.md" if record.log_dir else ""

        cost_usd: float | None = None
        usage_source = "unavailable"
        if record.usage is not None and record.usage.total_cost_usd is not None:
            cost_usd = record.usage.total_cost_usd
            usage_source = "api"

        # M1b PR 6: thread sealed EvalResult artefact refs onto the node.
        # Set lazily by _write_eval_result_artifact in the iteration that
        # produced this record; cleared after consumption.
        eval_result_ref = getattr(self, "_pending_eval_result_ref", None)
        eval_result_sha256 = getattr(self, "_pending_eval_result_sha256", None)

        # M2 PR 13: ask the agent for its identity if it exposes one;
        # fall back to "claude_sdk" for the legacy ClaudeCodeAgent which
        # doesn't have backend_kind/backend_version properties.
        backend_kind = getattr(self.agent, "backend_kind", None) or "claude_sdk"
        backend_version = getattr(self.agent, "backend_version", None) or ""
        # M3 PR 17: propagate AgentResult.backend_metadata onto the
        # AttemptNode so reporters / audit tools can render the
        # truth-in-labeling banners (cli_subscription_unsandboxed,
        # stale compliance) without spelunking through the run dir.
        bm = _extract_backend_metadata(agent_result)

        node = AttemptNode(
            id=attempt_id,
            parent_id=parent_id,
            commit=record.commit or "",
            backend_kind=backend_kind,
            backend_version=backend_version,
            model=getattr(self.agent, "model", "") or "",
            cli_binary_path=bm.get("cli_binary_path"),
            cli_version=bm.get("cli_version"),
            cli_argv=bm.get("cli_argv"),
            env_allowlist=bm.get("env_allowlist", []),
            isolation=bm.get("isolation"),
            compliance_report_path=bm.get("compliance_report_path"),
            prompt_digest="",  # populated when prompt hashing lands (M1b)
            prompt_ref=prompt_ref,
            diff_text=diff_inline,
            diff_ref=diff_ref,
            eval_result_ref=eval_result_ref,
            eval_result_sha256=eval_result_sha256,
            outcome=record.status,
            node_state="frontier",
            cost_usd=cost_usd,
            usage_source=usage_source,
            created_at=record.timestamp or "",
            worktree_path=str(self.workspace),
        )

        # M1b PR 8a (reviewer F3): parent_id is CODE ancestry, not sequence
        # ancestry. Only "keep" outcomes advance the parent pointer because
        # only kept nodes leave a commit on the branch. discard / crash /
        # violation / skip nodes still appear in the ledger (with this same
        # parent_id), but the NEXT attempt runs from the previous kept
        # state, so its parent must point at the kept node — not the
        # rejected one.
        if record.status == "keep":
            self._last_attempt_id_by_beam[beam] = attempt_id
        return node

    def _dual_log(
        self,
        record: ExperimentRecord,
        agent_result,
        diff_text: str | None = None,
    ) -> None:
        """Write to ResultsLog and TrialLedger atomically (best-effort)."""
        self.results.log(record)
        try:
            node = self._record_to_attempt_node(record, agent_result, diff_text)
            self.ledger.append_node(node)
        except Exception as exc:  # never let ledger errors break the loop
            logger.warning("ledger append failed: %s", exc)

    def _ledger_log_no_commit(
        self,
        outcome: str,
        agent_result,
        description: str,
    ) -> None:
        """Record a "violation" or "skip" outcome to the ledger only.

        These outcomes never go through _dual_log because the orchestrator
        early-returns before any ResultsLog.log() call (no commit happened,
        no metric was measured). Existing behavior — ResultsLog is silent on
        these paths — is preserved; we only add the new ledger entry.
        """
        seq = self._iteration if self._iteration is not None else 0
        beam = self._current_beam_id
        if beam is None:
            attempt_id = AttemptNode.short_id(seq)
        else:
            attempt_id = f"b{beam}{AttemptNode.short_id(seq)}"
        parent_id = self._last_attempt_id_by_beam.get(beam)

        cost_usd: float | None = None
        usage_source = "unavailable"
        if (
            agent_result is not None
            and agent_result.usage is not None
            and agent_result.usage.total_cost_usd is not None
        ):
            cost_usd = agent_result.usage.total_cost_usd
            usage_source = "api"

        try:
            backend_kind = getattr(self.agent, "backend_kind", None) or "claude_sdk"
            backend_version = getattr(self.agent, "backend_version", None) or ""
            bm = _extract_backend_metadata(agent_result)
            node = AttemptNode(
                id=attempt_id,
                parent_id=parent_id,
                commit="",  # no commit happened
                backend_kind=backend_kind,
                backend_version=backend_version,
                model=getattr(self.agent, "model", "") or "",
                cli_binary_path=bm.get("cli_binary_path"),
                cli_version=bm.get("cli_version"),
                cli_argv=bm.get("cli_argv"),
                env_allowlist=bm.get("env_allowlist", []),
                isolation=bm.get("isolation"),
                compliance_report_path=bm.get("compliance_report_path"),
                prompt_digest="",
                prompt_ref=f"logs/iter-{seq}/prompt.md",
                diff_text="",
                diff_ref="",
                outcome=outcome,
                node_state="frontier",
                cost_usd=cost_usd,
                usage_source=usage_source,
                created_at=datetime.now(timezone.utc).isoformat(),
                worktree_path=str(self.workspace),
                description=description[:500] if description else None,
            )
            # M1b PR 8a: violation/skip do NOT advance the code-parent
            # pointer. The next attempt runs from the previous kept state,
            # so its parent must point at the kept node.
            self.ledger.append_node(node)
        except Exception as exc:
            logger.warning("ledger append failed (no-commit outcome %s): %s",
                           outcome, exc)

    def _read_last_agent_log(self) -> str | None:
        """Read the previous iteration's agent reasoning log."""
        prev = self._iteration - 1
        if prev < 1:
            return None
        log_path = self.workspace / "logs" / f"iter-{prev}" / "agent.txt"
        if log_path.exists():
            return log_path.read_text()
        return None

    def _save_iteration_logs(
        self,
        iteration: int,
        agent_result,
        prompt: str | None = None,
        diff_text: str | None = None,
    ) -> None:
        """Save agent output, run.log, and (M1a) prompt + diff artefacts.

        prompt.md and diff.patch are referenced by AttemptNode.prompt_ref /
        diff_ref. Per reviewer F3: orchestrator MUST write these files if
        AttemptNode is going to point at them, otherwise HTML report has
        broken links.
        """
        log_dir = self.workspace / "logs" / f"iter-{iteration}"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Save agent reasoning
        if agent_result.agent_output:
            (log_dir / "agent.txt").write_text(agent_result.agent_output)

        # Copy run.log if exists
        run_log = self.workspace / "run.log"
        if run_log.exists():
            shutil.copy2(run_log, log_dir / "run.log")

        # M1a: persist prompt + diff for the ledger's *_ref fields
        if prompt is not None:
            (log_dir / "prompt.md").write_text(prompt)
        if diff_text:
            (log_dir / "diff.patch").write_text(diff_text)

    def _install_requirements(self):
        """Install packages from requirements.txt."""
        req = self.workspace / "requirements.txt"
        if not req.exists():
            return
        logger.info(_("Installing updated requirements..."))
        env = self.runner._make_env()
        result = subprocess.run(
            ["python3", "-m", "pip", "install", "-r", str(req)],
            cwd=self.workspace, env=env,
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

    def _count_plateau_streak(self, results: ResultsLog | None = None) -> int:
        """Count consecutive non-keep records from the end of results."""
        return ResultsLog.plateau_streak((results or self.results).read_all())

    def _check_convergence(self, results: ResultsLog | None = None) -> bool:
        """Check if the experiment has converged (no meaningful improvement)."""
        window = self.config.constraints.convergence_window
        if window is None:
            return False
        results = results or self.results
        records = [r for r in results.read_all() if r.status != "baseline"]
        if len(records) < window:
            return False

        # Path 1: no keeps at all in the last N iterations
        if self._count_plateau_streak(results) >= window:
            return True

        # Path 2: recent keeps are all negligible improvements
        min_imp = self.config.constraints.min_improvement
        if min_imp is not None:
            kept_in_window = [r for r in records[-window:] if r.status == "keep"]
            measurable = [r for r in kept_in_window if r.delta_percent is not None]
            if measurable and all(
                abs(r.delta_percent) < min_imp * 100 for r in measurable
            ):
                return True

        return False

    def init_beams(self) -> None:
        """Initialize beam branches and per-beam state. Call after init()."""
        beam_width = self.config.search.beam_width
        self.git.create_beam_branches(self.tag, beam_width)
        self._beams = []
        for i in range(beam_width):
            beam_branch = f"{self.config.git.branch_prefix}/{self.tag}-beam-{i}"
            beam_results = ResultsLog(
                self.workspace / f"results-{self.tag}-beam-{i}.jsonl"
            )
            beam_results.init()
            beam_context = ContextAssembler(
                config=self.config,
                project_root=self.workspace,
                branch_name=beam_branch,
            )
            self._beams.append(BeamState(
                beam_id=i,
                results=beam_results,
                context=beam_context,
            ))
        self._current_beam_idx = 0

    def run_loop(self, max_iterations: int | None = None) -> None:
        """Run iterations until stopped, budget exceeded, or max_iterations reached."""
        strategy = self.config.search.strategy
        if strategy == "beam":
            self._run_loop_beam(max_iterations)
        else:
            self._run_loop_serial(max_iterations)

    def _run_loop_serial(self, max_iterations: int | None = None) -> None:
        """Serial loop for greedy and restart strategies."""
        import platform as _platform
        from crucible.locking import WorktreeMutex, WorktreeLocked

        if max_iterations is None:
            max_iterations = self.config.constraints.max_iterations

        strategy = self.config.search.strategy
        plateau_threshold = self.config.search.plateau_threshold
        max_retries = self.config.constraints.max_retries
        session_count = 0
        # M2 PR 14: hold the worktree mutex for the whole serial run.
        # In single-process mode this is a near-free no-op; the
        # invariant ("one attempt per worktree at any time") is what
        # future parallel BFTS workers will rely on. Timeout default is
        # short (5s) — if another process is here, we want to fail
        # loudly rather than block.
        #
        # Reviewer round 2 F4: Windows is explicitly unsupported (matches
        # `TrialLedger`'s POSIX-flock-only stance). On Windows we DO NOT
        # silently swallow the failure — we skip the mutex entirely and
        # log a clear warning so operators understand the cross-process
        # invariant is not enforced.
        mutex: WorktreeMutex | None = None
        if _platform.system() == "Windows":
            logger.warning(
                "WorktreeMutex unsupported on Windows in v1.0 — running in "
                "single-process mode without cross-process lock enforcement. "
                "Concurrent crucible runs against the same workspace will "
                "race; use one process at a time."
            )
        else:
            try:
                mutex = WorktreeMutex(self.workspace, timeout=5.0)
                mutex.acquire()
            except WorktreeLocked as exc:
                logger.error(
                    "Cannot start serial loop — worktree is already locked: %s",
                    exc,
                )
                return
        try:
            while True:
                if max_iterations is not None and session_count >= max_iterations:
                    logger.info(_("Reached max iterations ({n}), stopping.").format(n=max_iterations))
                    break

                logger.info(f"--- iter {self._iteration + 1} ---")
                status = self.run_one_iteration()
                session_count += 1

                if status == "fatal":
                    logger.error(_FATAL_MSG)
                    break

                best = self.results.best(self.config.metric.direction)
                best_str = f"{best.metric_value}" if best else "N/A"
                logger.info(f"[iter {self._iteration}] {status} | best {self.config.metric.name}: {best_str}")

                if self._profile and self.context.prompt_breakdown:
                    bd = self.context.prompt_breakdown
                    total = bd.get("total", 1)
                    parts = []
                    for k, v in bd.items():
                        if k != "total" and v > 0:
                            pct = v * 100 // total
                            parts.append(f"{k}: {pct}%")
                    last_records = self.results.read_all()
                    last_usage = last_records[-1].usage if last_records else None
                    cache_str = ""
                    if last_usage:
                        cp = last_usage.cache_hit_percent()
                        if cp is not None:
                            cache_str = f" | cache: {cp}%"
                    logger.info(f"[profile] prompt: ~{total} tok ({', '.join(parts)}){cache_str}")

                if status == "budget_exceeded":
                    logger.warning(_("Budget limit reached, stopping."))
                    break

                if status in ("skip", "violation"):
                    self._consecutive_skips += 1
                else:
                    self._consecutive_skips = 0

                # M1b PR 8c (reviewer F2): SearchStrategy gets the FIRST
                # word on what to do next. BFTS may decide to BranchFrom
                # the best kept node even after a streak of crashes —
                # that is a normal search event, not a reason to stop.
                # Greedy/Restart strategies preserve old behavior because
                # their decide() never returns BranchFrom; the legacy
                # max_retries / convergence checks below still fire for them.
                strategy_action = None
                if self.strategy is not None:
                    sctx = self._build_strategy_context(
                        session_count=session_count,
                        plateau_threshold=plateau_threshold,
                        max_iterations=max_iterations,
                    )
                    streak = sctx.plateau_streak
                    strategy_action = self.strategy.decide(sctx)
                    # M3 PR 17: record decision to the sidecar log so
                    # `crucible postmortem --strategy-decisions` can
                    # explain why BFTS branched / pruned / stopped at
                    # each iteration. Best-effort — never raise.
                    self._log_strategy_decision(sctx, strategy_action)
                    # BranchFrom + Restart need to pre-empt legacy stop checks.
                    if isinstance(strategy_action, BranchFrom):
                        target_commit = self._lookup_commit_for_node(strategy_action.parent_id)
                        if target_commit is not None:
                            logger.info(
                                f"[iter {self._iteration}] "
                                f"BranchFrom({strategy_action.parent_id}) "
                                f"commit={target_commit[:7]}"
                                f"{f' — {strategy_action.reason}' if strategy_action.reason else ''}"
                            )
                            self.git.reset_to_commit(target_commit)
                            self.context.add_error(
                                f"⤴ BRANCH — search strategy redirected to attempt "
                                f"{strategy_action.parent_id} (commit {target_commit[:7]}). "
                                f"Reasoning carried forward from that point."
                            )
                            self._last_attempt_id_by_beam[None] = strategy_action.parent_id
                            self._consecutive_failures = 0
                            self._consecutive_skips = 0
                            continue  # bypass legacy stops; next iter runs from new commit
                        logger.warning(
                            f"[strategy] BranchFrom({strategy_action.parent_id}) "
                            f"could not resolve commit; falling through to Continue"
                        )

                if self._consecutive_failures >= max_retries:
                    logger.warning(f"[iter {self._iteration}] " + _("{n} consecutive failures, stopping.").format(n=max_retries))
                    break
                if self._consecutive_skips >= max_retries:
                    logger.warning(f"[iter {self._iteration}] " + _("{n} consecutive skips, stopping.").format(n=max_retries))
                    break

                if self._check_convergence():
                    logger.info(
                        f"[iter {self._iteration}] Converged — no meaningful improvement "
                        f"for {self.config.constraints.convergence_window} iterations, stopping."
                    )
                    break

                # M1b PR 2+3: handle the remaining strategy actions
                # (Continue / Restart / Stop) after legacy stops have had
                # their say. BranchFrom was already handled above.
                if self.strategy is not None and strategy_action is not None:
                    action = strategy_action
                    if isinstance(action, StrategyStop):
                        logger.info(f"[strategy] {action.reason}")
                        break
                    elif isinstance(action, StrategyRestart):
                        if self._baseline_commit:
                            logger.info(
                                f"[iter {self._iteration}] Plateau ({streak} iters) — "
                                "restarting from baseline"
                            )
                            self.git.reset_to_commit(self._baseline_commit)
                            self.context.add_error(
                                f"⟳ RESTART — {streak} iterations without improvement. "
                                "Returning to baseline. Your full history is preserved above. "
                                "Choose a completely different direction."
                            )
                            self._consecutive_failures = 0
                            self._consecutive_skips = 0
                    # BranchFrom was handled before legacy stops (PR 8c).
                    # Continue → fall through to next iteration.
                elif strategy == "restart" and self._baseline_commit:
                    # Legacy path (only reached if make_strategy failed).
                    streak = self._count_plateau_streak()
                    if streak >= plateau_threshold:
                        logger.info(
                            f"[iter {self._iteration}] Plateau ({streak} iters) — "
                            "restarting from baseline"
                        )
                        self.git.reset_to_commit(self._baseline_commit)
                        self.context.add_error(
                            f"⟳ RESTART — {streak} iterations without improvement. "
                            "Returning to baseline. Your full history is preserved above. "
                            "Choose a completely different direction."
                        )
                        self._consecutive_failures = 0

        except KeyboardInterrupt:
            logger.info(_("Stopped after {n} iterations.").format(n=self._iteration))
        finally:
            # Release the worktree mutex held for the whole serial run.
            if mutex is not None and mutex.held:
                mutex.release()

    @contextmanager
    def _beam_swap(self, beam: BeamState):
        """Temporarily swap orchestrator state to a beam, restoring on exit."""
        orig = (self.results, self.context, self._fail_seq,
                self._consecutive_failures, self._consecutive_skips, self._iteration)
        self._current_beam_id = beam.beam_id
        self.results = beam.results
        self.context = beam.context
        self._fail_seq = beam.fail_seq
        self._consecutive_failures = beam.consecutive_failures
        self._consecutive_skips = beam.consecutive_skips
        self._iteration = beam.iteration
        try:
            yield
        finally:
            beam.fail_seq = self._fail_seq
            beam.consecutive_failures = self._consecutive_failures
            beam.consecutive_skips = self._consecutive_skips
            beam.iteration = self._iteration
            (self.results, self.context, self._fail_seq,
             self._consecutive_failures, self._consecutive_skips, self._iteration) = orig
            self._current_beam_id = None

    def _run_loop_beam(self, max_iterations: int | None = None) -> None:
        """Beam search: round-robin across beam_width branches."""
        if max_iterations is None:
            max_iterations = self.config.constraints.max_iterations
        max_retries = self.config.constraints.max_retries
        session_count = 0

        try:
            while True:
                if max_iterations is not None and session_count >= max_iterations:
                    break

                # All beams stopped (exhausted or converged)?
                if self._beams and all(
                    b.consecutive_failures >= max_retries
                    or self._check_convergence(b.results)
                    for b in self._beams
                ):
                    for b in self._beams:
                        reason = "converged" if self._check_convergence(b.results) else "exhausted"
                        logger.info(f"beam-{b.beam_id}: {reason}")
                    logger.info(_("All beams stopped — stopping."))
                    break

                # Pick next beam (round-robin, skip exhausted beams)
                if not self._beams:
                    logger.warning(_("No beams initialized — falling back to serial loop."))
                    self._run_loop_serial(max_iterations)
                    return

                beam = self._beams[self._current_beam_idx % len(self._beams)]
                self._current_beam_idx += 1
                # TODO: cross-beam early stop — skip beam if its best trails
                # global best by a large margin and it has plateaued
                if beam.consecutive_failures >= max_retries:
                    continue
                if self._check_convergence(beam.results):
                    continue

                # Checkout beam branch
                self.git.checkout_beam(self.tag, beam.beam_id)

                # Build cross-beam summaries for OTHER beams
                other_summaries = []
                for b in self._beams:
                    if b.beam_id == beam.beam_id:
                        continue
                    best_rec = b.results.best(self.config.metric.direction)
                    other_summaries.append({
                        "beam_id": b.beam_id,
                        "best": best_rec.metric_value if best_rec else None,
                        "tried": b.results.read_all(),
                    })

                # Inject cross-beam context
                beam.context._beam_summaries = other_summaries

                with self._beam_swap(beam):
                    status = self.run_one_iteration()
                session_count += 1

                if status == "fatal":
                    logger.error(_FATAL_MSG)
                    break

                best = beam.results.best(self.config.metric.direction)
                best_str = f"{best.metric_value}" if best else "N/A"
                logger.info(
                    f"[beam-{beam.beam_id} iter {beam.iteration}] {status} "
                    f"| best {self.config.metric.name}: {best_str}"
                )

                if status == "budget_exceeded":
                    break

        except KeyboardInterrupt:
            logger.info(_("Stopped."))


# ---------------------------------------------------------------------------
# M3 PR 17 helpers
# ---------------------------------------------------------------------------


def _extract_backend_metadata(agent_result) -> dict:
    """Return `agent_result.backend_metadata` if present, else `{}`.

    M3 PR 17: backends like SubscriptionCLIBackend populate
    `AgentResult.backend_metadata` with cli_binary_path / cli_version /
    cli_argv / env_allowlist / isolation / compliance_report_path. The
    orchestrator copies these onto AttemptNode so reporters can render
    truth-in-labeling banners. Defensive default for legacy backends
    (ClaudeCodeAgent / SmolagentsBackend) that don't populate it.
    """
    if agent_result is None:
        return {}
    meta = getattr(agent_result, "backend_metadata", None)
    return meta if isinstance(meta, dict) else {}
