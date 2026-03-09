"""Claude Code agent — invokes the `claude` CLI to generate edits."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from crucible.agents.base import AgentInterface, AgentResult

DEFAULT_AGENT_TIMEOUT = 600

SYSTEM_PROMPT = (
    "You are an autonomous code optimization agent. "
    "You MUST use the Read tool to examine files, then use the Edit tool to modify them. "
    "Do NOT just describe or explain changes — you must actually edit the files using tools. "
    "After editing, output a one-line summary of what you changed."
)


def _stream_pipe(pipe, lines: list[str], prefix: str = "") -> None:
    try:
        for line in pipe:
            lines.append(line)
            sys.stdout.write(f"{prefix}{line}")
            sys.stdout.flush()
    except ValueError:
        pass


class ClaudeCodeAgent(AgentInterface):
    def __init__(self, timeout: int = DEFAULT_AGENT_TIMEOUT, model: str | None = None):
        self.timeout = timeout
        self.model = model

    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        cmd = [
            "claude",
            "--print",
            "--permission-mode", "bypassPermissions",
            "--system-prompt", SYSTEM_PROMPT,
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.append(prompt)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        try:
            proc = subprocess.Popen(
                cmd, cwd=workspace,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            t_out = threading.Thread(
                target=_stream_pipe, args=(proc.stdout, stdout_lines, "  "), daemon=True,
            )
            t_err = threading.Thread(
                target=_stream_pipe, args=(proc.stderr, stderr_lines, "  [err] "), daemon=True,
            )
            t_out.start()
            t_err.start()
            proc.wait(timeout=self.timeout)
            t_out.join(timeout=3)
            t_err.join(timeout=3)
            returncode = proc.returncode
        except FileNotFoundError:
            return AgentResult(modified_files=[], description="claude CLI not found")
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return AgentResult(modified_files=[], description="claude CLI timed out")
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
            raise

        stdout = "".join(stdout_lines).strip()
        stderr = "".join(stderr_lines).strip()

        if returncode != 0:
            desc = stderr.split("\n")[0][:200] if stderr else "claude CLI error"
            return AgentResult(modified_files=[], description=desc)

        description = stdout.split("\n")[0][:200] if stdout else "no description"

        diff_result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=workspace, capture_output=True, text=True,
        )
        untracked_result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=workspace, capture_output=True, text=True,
        )
        changed = diff_result.stdout.strip().splitlines()
        untracked = untracked_result.stdout.strip().splitlines()
        changed = [f for f in changed if "__pycache__/" not in f]
        untracked = [f for f in untracked if "__pycache__/" not in f]
        all_files = [Path(f) for f in changed + untracked if f]

        if not all_files:
            print(f"  [agent] no files changed")
        else:
            print(f"  [agent] modified: {[str(f) for f in all_files]}")
        return AgentResult(modified_files=all_files, description=description)
