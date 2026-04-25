"""Sandbox execution environment for experiment commands."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from crucible.config import SandboxConfig
from crucible.i18n import _
from crucible.runner import ExperimentRunner, RunResult, run_with_repeat

logger = logging.getLogger(__name__)

# Env files that are always shadow-mounted to /dev/null in Docker for security.
# These must never appear in editable_files mounts — last -v wins in Docker.
_SHADOWED_ENV_FILES: frozenset[str] = frozenset({
    ".env", ".env.local", ".env.production", ".env.staging",
})


def check_docker_available() -> bool:
    """Check if Docker daemon is available."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


class SandboxRunner:
    """Wraps ExperimentRunner with optional Docker sandboxing."""

    def __init__(
        self,
        config: SandboxConfig | None,
        workspace: Path,
        editable_files: list[str] | None = None,
        artifact_dirs: list[str] | None = None,
    ) -> None:
        self.config = config or SandboxConfig(backend="none")
        self.workspace = Path(workspace)
        self.editable_files = editable_files or []
        self.artifact_dirs = artifact_dirs or []
        self._native = ExperimentRunner(workspace=workspace)
        self._cached_hash: str | None = None

    def execute(self, command: str, timeout: int) -> RunResult:
        if self.config.backend == "docker":
            return self._docker_run(command, timeout)
        return self._native.execute(command, timeout)

    def parse_metric(self, eval_command: str, metric_name: str, timeout: int = 30):
        """Parse metric from eval command output.

        M1b: when backend=="docker", the eval command now runs INSIDE the
        same isolation as run_cmd (closing the trust break flagged by the
        v3.2 spec review at sandbox.py:59-64). The host process parses
        the captured stdout, so the platform — not the agent — owns
        metric extraction. For backend=="none", behavior is unchanged.

        Returns the float metric value, or None if no match.
        """
        if self.config.backend == "docker":
            run_result = self._docker_run(eval_command, timeout)
            import re
            pattern = re.compile(rf"^{re.escape(metric_name)}:\s*(.+)$", re.MULTILINE)
            match = pattern.search(run_result.stdout)
            if match:
                try:
                    return float(match.group(1).strip())
                except ValueError:
                    return None
            return None
        return self._native.parse_metric(eval_command, metric_name, timeout)

    def execute_with_repeat(
        self,
        run_cmd: str,
        eval_cmd: str,
        metric_name: str,
        repeat: int,
        aggregation: str,
        timeout: int,
    ) -> tuple[RunResult, float | None]:
        """Multi-run support delegates to shared implementation."""
        return run_with_repeat(self, run_cmd, eval_cmd, metric_name, repeat, aggregation, timeout)

    def _docker_run(self, command: str, timeout: int) -> RunResult:
        """Run command inside Docker container."""
        image = self._ensure_image()
        cmd = ["docker", "run", "--rm"]

        # Resource limits
        if self.config.memory_limit:
            cmd.extend(["--memory", self.config.memory_limit])
        if self.config.cpu_limit:
            cmd.extend(["--cpus", str(self.config.cpu_limit)])

        # Network
        if not self.config.network:
            cmd.extend(["--network", "none"])

        # Mount workspace as readonly
        cmd.extend(["-v", f"{self.workspace}:/workspace:ro", "-w", "/workspace"])

        # Shadow .env variants with /dev/null so agent-generated code cannot
        # read secrets even if the hook is bypassed (defense-in-depth).
        # Docker-mode only. Native mode relies on the PreToolUse hook.
        for env_name in _SHADOWED_ENV_FILES:
            if (self.workspace / env_name).exists():
                cmd.extend(["-v", f"/dev/null:/workspace/{env_name}:ro"])

        # Mount editable files as read-write (override readonly).
        # Skip any files in _SHADOWED_ENV_FILES — the shadow mount must win.
        for f in self.editable_files:
            if f in _SHADOWED_ENV_FILES:
                continue
            fpath = self.workspace / f
            if fpath.exists():
                cmd.extend(["-v", f"{fpath}:/workspace/{f}:rw"])

        # Mount artifact directories as read-write
        for d in self.artifact_dirs:
            dpath = self.workspace / d
            dpath.mkdir(parents=True, exist_ok=True)
            cmd.extend(["-v", f"{dpath}:/workspace/{d}:rw"])

        # Ensure run.log is writable (touch first so Docker doesn't create a directory)
        run_log = self.workspace / "run.log"
        if not run_log.exists():
            run_log.touch()
        cmd.extend(["-v", f"{run_log}:/workspace/run.log:rw"])

        cmd.extend([image, "bash", "-c", command])

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return RunResult(
                exit_code=proc.returncode,
                timed_out=False,
                stderr_tail="\n".join(stderr.splitlines()[-50:]),
                stdout=stdout,
            )
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                _, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, stderr = proc.communicate()
            return RunResult(
                exit_code=-1,
                timed_out=True,
                stderr_tail="\n".join(stderr.splitlines()[-50:]),
                stdout="",
            )

    def _ensure_image(self) -> str:
        """Build or return cached Docker image with project deps."""
        tag = f"crucible-{self.workspace.name}:latest"
        dep_hash = self._hash_deps()

        if self._cached_hash == dep_hash:
            return tag

        dockerfile = f"FROM {self.config.base_image}\nWORKDIR /workspace\n"
        req = self.workspace / "requirements.txt"
        pyproject = self.workspace / "pyproject.toml"

        if req.exists():
            dockerfile += (
                "COPY requirements.txt .\n"
                "RUN pip install --no-cache-dir -r requirements.txt\n"
            )
        elif pyproject.exists():
            dockerfile += (
                "COPY pyproject.toml .\n"
                "RUN pip install --no-cache-dir .\n"
            )

        logger.info(_("Building Docker image {tag}...").format(tag=tag))
        result = subprocess.run(
            ["docker", "build", "-t", tag, "-f-", "."],
            input=dockerfile,
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(_("Docker build failed: {error}").format(error=result.stderr[-500:]))
            raise RuntimeError(_("Docker image build failed: {error}").format(error=result.stderr[-200:]))

        self._cached_hash = dep_hash
        return tag

    def _hash_deps(self) -> str:
        """Hash dependency files for cache invalidation."""
        h = hashlib.sha256()
        for name in ("requirements.txt", "pyproject.toml"):
            path = self.workspace / name
            if path.exists():
                h.update(path.read_bytes())
        return h.hexdigest()[:16]
