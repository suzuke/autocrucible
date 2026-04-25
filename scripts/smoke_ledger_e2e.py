"""M1a PR 1+2 end-to-end smoke (zero LLM token).

Builds a tiny example workspace, plugs in a deterministic fake agent that
makes a one-line edit, runs 2 orchestrator iterations, and inspects the
resulting ledger.jsonl. No CC subscription quota consumed.

Usage:
    python scripts/smoke_ledger_e2e.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from crucible.agents.base import AgentInterface, AgentResult
from crucible.config import Config, load_config
from crucible.ledger import TrialLedger
from crucible.orchestrator import Orchestrator
from crucible.results import UsageInfo


CONFIG_YAML = """\
name: smoke
files:
  editable: [solution.py]
  readonly: [evaluate.py]
commands:
  run: "python3 -u evaluate.py 2>&1 | tee run.log"
  eval: "cat run.log"
metric:
  name: metric
  direction: maximize
constraints:
  timeout_seconds: 30
search:
  strategy: greedy
git:
  branch_prefix: smoke
"""


SOLUTION_PY = """\
def f(x):
    return x
"""


EVALUATE_PY = """\
import math
import os
import sys
sys.path.insert(0, '.')
import solution

# Fake metric: count unique outputs of solution.f(0..9). More unique → "better".
unique = len({solution.f(i) for i in range(10)})
print(f'metric: {unique / 10.0:.4f}')
"""


class FakeAgent(AgentInterface):
    """Returns a deterministic edit each time it's called.

    Iteration 1: change solution.f to return x*2 (unique: 10 → metric 1.0).
    Iteration 2: change solution.f to return x   (unique: 10 → same metric).

    Each call records the iteration number for assertion.
    """

    model = "fake-agent-1"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.calls = 0

    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        self.calls += 1
        sol = workspace / "solution.py"
        if self.calls == 1:
            sol.write_text("def f(x):\n    return x * 2\n")
            desc = "iter1: multiply by 2"
        else:
            sol.write_text("def f(x):\n    return x + 1\n")
            desc = "iter2: add 1"
        # orchestrator.guardrails compares against config.files.editable
        # which uses relative paths — return Path('solution.py'), not the
        # absolute workspace/solution.py.
        return AgentResult(
            modified_files=[Path("solution.py")],
            description=desc,
            usage=UsageInfo(total_cost_usd=0.001, num_turns=1),
            duration_seconds=0.01,
            agent_output=f"FakeAgent call #{self.calls}",
        )


def main() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="crucible-smoke-"))
    try:
        # Layout
        (workspace / "solution.py").write_text(SOLUTION_PY)
        (workspace / "evaluate.py").write_text(EVALUATE_PY)
        (workspace / ".crucible").mkdir()
        (workspace / ".crucible" / "config.yaml").write_text(CONFIG_YAML)

        # Init git so orchestrator's GitManager works
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
        subprocess.run(
            ["git", "-c", "user.email=smoke@test", "-c", "user.name=smoke",
             "commit", "-q", "-m", "init"],
            cwd=workspace, check=True,
        )

        config = load_config(workspace)
        agent = FakeAgent(workspace)
        orchestrator = Orchestrator(
            config=config, workspace=workspace, tag="smoke", agent=agent,
        )
        orchestrator.init()

        # Run 2 iterations directly through the loop
        for i in range(2):
            try:
                outcome = orchestrator.run_one_iteration()
                print(f"iter {i+1}: outcome={outcome}")
            except Exception as exc:
                print(f"iter {i+1}: raised {type(exc).__name__}: {exc}")
                break

        # Inspect ledger
        ledger_path = workspace / "logs" / "run-smoke" / "ledger.jsonl"
        print()
        print(f"=== ledger.jsonl at {ledger_path} ===")
        if not ledger_path.exists():
            print("(ledger not created — check orchestrator wiring)")
            return 1

        ledger = TrialLedger(ledger_path)
        nodes = ledger.all_nodes()
        print(f"Records: {len(nodes)} attempt nodes")
        for n in nodes:
            print(f"  {n.id}: outcome={n.outcome} parent={n.parent_id} "
                  f"commit={n.commit[:7]} cost={n.cost_usd}")

        print()
        print(f"=== raw ledger.jsonl content ===")
        print(ledger_path.read_text())

        # Also inspect the existing ResultsLog for parity
        results_path = workspace / "results-smoke.jsonl"
        if results_path.exists():
            print(f"=== results-smoke.jsonl ===")
            print(results_path.read_text())

        return 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
