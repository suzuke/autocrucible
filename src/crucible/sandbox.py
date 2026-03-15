"""Sandbox execution environment for experiment commands."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from crucible.config import SandboxConfig
from crucible.runner import ExperimentRunner, RunResult, run_with_repeat

logger = logging.getLogger(__name__)


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
        readonly_files: list[str] | None = None,
        hidden_files: list[str] | None = None,
        editable_files: list[str] | None = None,
    ) -> None:
        self.config = config or SandboxConfig(backend="none")
        self.workspace = Path(workspace)
        self.readonly_files = readonly_files or []
        self.hidden_files = hidden_files or []
        self.editable_files = editable_files or []
        self._native = ExperimentRunner(workspace=workspace)
        self._cached_hash: str | None = None

    def execute(self, command: str, timeout: int) -> RunResult:
        if self.config.backend == "docker":
            return self._docker_run(command, timeout)
        return self._native.execute(command, timeout)

    def parse_metric(self, eval_command: str, metric_name: str):
        """Parse metric -- always runs natively (reads run.log from host)."""
        return self._native.parse_metric(eval_command, metric_name)

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

        # Mount editable files as read-write (override readonly)
        for f in self.editable_files:
            fpath = self.workspace / f
            if fpath.exists():
                cmd.extend(["-v", f"{fpath}:/workspace/{f}:rw"])

        # Ensure run.log is writable
        cmd.extend(["-v", f"{self.workspace}/run.log:/workspace/run.log:rw"])

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

        logger.info(f"Building Docker image {tag}...")
        result = subprocess.run(
            ["docker", "build", "-t", tag, "-f-", "."],
            input=dockerfile,
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(f"Docker build failed: {result.stderr[-500:]}")
            raise RuntimeError(f"Docker image build failed: {result.stderr[-200:]}")

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
