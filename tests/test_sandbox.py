"""Tests for sandbox module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crucible.config import SandboxConfig
from crucible.runner import RunResult
from crucible.sandbox import SandboxRunner, check_docker_available


# --- check_docker_available ---


def test_check_docker_available_true():
    with patch("crucible.sandbox.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert check_docker_available() is True


def test_check_docker_available_false_returncode():
    with patch("crucible.sandbox.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert check_docker_available() is False


def test_check_docker_available_not_found():
    with patch("crucible.sandbox.subprocess.run", side_effect=FileNotFoundError):
        assert check_docker_available() is False


def test_check_docker_available_timeout():
    with patch(
        "crucible.sandbox.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=5),
    ):
        assert check_docker_available() is False


# --- SandboxRunner with backend=none ---


def test_none_backend_delegates_to_native(tmp_path):
    runner = SandboxRunner(config=None, workspace=tmp_path)
    assert runner.config.backend == "none"

    with patch.object(runner._native, "execute") as mock_exec:
        mock_exec.return_value = RunResult(exit_code=0, timed_out=False)
        result = runner.execute("echo hi", timeout=10)
        mock_exec.assert_called_once_with("echo hi", 10)
        assert result.exit_code == 0


def test_none_backend_with_explicit_config(tmp_path):
    cfg = SandboxConfig(backend="none")
    runner = SandboxRunner(config=cfg, workspace=tmp_path)
    with patch.object(runner._native, "execute") as mock_exec:
        mock_exec.return_value = RunResult(exit_code=0, timed_out=False)
        runner.execute("ls", timeout=5)
        mock_exec.assert_called_once()


def test_parse_metric_always_native(tmp_path):
    cfg = SandboxConfig(backend="docker")
    runner = SandboxRunner(config=cfg, workspace=tmp_path)
    with patch.object(runner._native, "parse_metric", return_value=0.5) as mock_pm:
        val = runner.parse_metric("python eval.py", "accuracy")
        mock_pm.assert_called_once_with("python eval.py", "accuracy")
        assert val == 0.5


# --- Docker backend command construction ---


def test_docker_run_builds_correct_command(tmp_path):
    cfg = SandboxConfig(
        backend="docker",
        base_image="python:3.11",
        network=False,
        memory_limit="2g",
        cpu_limit=4,
    )
    runner = SandboxRunner(
        config=cfg,
        workspace=tmp_path,
    )
    # Create files so readonly mount logic triggers
    (tmp_path / "eval.py").write_text("pass")
    (tmp_path / "secret.py").write_text("pass")

    with patch.object(runner, "_ensure_image", return_value="crucible-test:latest"):
        with patch("crucible.sandbox.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("", "")
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            result = runner._docker_run("python train.py", timeout=60)

            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "docker"
            assert cmd[1] == "run"
            assert "--rm" in cmd
            assert "--memory" in cmd
            idx = cmd.index("--memory")
            assert cmd[idx + 1] == "2g"
            assert "--cpus" in cmd
            idx = cmd.index("--cpus")
            assert cmd[idx + 1] == "4"
            assert "--network" in cmd
            idx = cmd.index("--network")
            assert cmd[idx + 1] == "none"
            assert "bash" in cmd
            assert "-c" in cmd
            assert "python train.py" in cmd
            assert result.exit_code == 0
            assert result.timed_out is False


def test_docker_run_mounts_artifact_dirs(tmp_path):
    cfg = SandboxConfig(backend="docker")
    runner = SandboxRunner(
        config=cfg,
        workspace=tmp_path,
        artifact_dirs=["artifacts/", "weights/"],
    )

    with patch.object(runner, "_ensure_image", return_value="crucible-test:latest"):
        with patch("crucible.sandbox.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("", "")
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            runner._docker_run("python train.py", timeout=60)

            cmd = mock_popen.call_args[0][0]
            # Verify artifact dirs are mounted as rw
            cmd_str = " ".join(str(c) for c in cmd)
            assert f"{tmp_path}/artifacts:/workspace/artifacts/:rw" in cmd_str
            assert f"{tmp_path}/weights:/workspace/weights/:rw" in cmd_str

            # Verify directories were created on host
            assert (tmp_path / "artifacts").is_dir()
            assert (tmp_path / "weights").is_dir()


def test_docker_run_network_enabled(tmp_path):
    cfg = SandboxConfig(backend="docker", network=True)
    runner = SandboxRunner(config=cfg, workspace=tmp_path)

    with patch.object(runner, "_ensure_image", return_value="crucible-test:latest"):
        with patch("crucible.sandbox.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("", "")
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            runner._docker_run("echo hi", timeout=10)

            cmd = mock_popen.call_args[0][0]
            assert "--network" not in cmd


def test_docker_run_timeout(tmp_path):
    cfg = SandboxConfig(backend="docker")
    runner = SandboxRunner(config=cfg, workspace=tmp_path)

    with patch.object(runner, "_ensure_image", return_value="crucible-test:latest"):
        with patch("crucible.sandbox.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.side_effect = [
                subprocess.TimeoutExpired(cmd="docker", timeout=10),
                ("", "some error\noutput"),
            ]
            mock_proc.returncode = -1
            mock_popen.return_value = mock_proc

            result = runner._docker_run("sleep 100", timeout=10)
            assert result.timed_out is True
            assert result.exit_code == -1


# --- _ensure_image ---


def test_ensure_image_with_requirements(tmp_path):
    cfg = SandboxConfig(backend="docker", base_image="python:3.12-slim")
    runner = SandboxRunner(config=cfg, workspace=tmp_path)
    (tmp_path / "requirements.txt").write_text("numpy==1.26\n")

    with patch("crucible.sandbox.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        tag = runner._ensure_image()

        assert tag == f"crucible-{tmp_path.name}:latest"
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        dockerfile_input = call_args.kwargs.get("input") or call_args[1].get("input", "")
        assert "requirements.txt" in dockerfile_input
        assert "python:3.12-slim" in dockerfile_input


def test_ensure_image_with_pyproject(tmp_path):
    cfg = SandboxConfig(backend="docker")
    runner = SandboxRunner(config=cfg, workspace=tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname="test"\n')

    with patch("crucible.sandbox.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        tag = runner._ensure_image()

        call_args = mock_run.call_args
        dockerfile_input = call_args.kwargs.get("input") or call_args[1].get("input", "")
        assert "pyproject.toml" in dockerfile_input


def test_ensure_image_caches(tmp_path):
    cfg = SandboxConfig(backend="docker")
    runner = SandboxRunner(config=cfg, workspace=tmp_path)
    (tmp_path / "requirements.txt").write_text("flask\n")

    with patch("crucible.sandbox.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        runner._ensure_image()
        runner._ensure_image()
        # Should only build once due to caching
        assert mock_run.call_count == 1


def test_ensure_image_rebuilds_on_dep_change(tmp_path):
    cfg = SandboxConfig(backend="docker")
    runner = SandboxRunner(config=cfg, workspace=tmp_path)
    (tmp_path / "requirements.txt").write_text("flask\n")

    with patch("crucible.sandbox.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        runner._ensure_image()
        # Change deps
        (tmp_path / "requirements.txt").write_text("flask\nrequests\n")
        runner._ensure_image()
        assert mock_run.call_count == 2


def test_ensure_image_build_failure(tmp_path):
    cfg = SandboxConfig(backend="docker")
    runner = SandboxRunner(config=cfg, workspace=tmp_path)

    with patch("crucible.sandbox.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="error: build failed")
        with pytest.raises(RuntimeError, match="Docker image build failed"):
            runner._ensure_image()


# --- _hash_deps ---


def test_hash_deps_changes_when_deps_change(tmp_path):
    runner = SandboxRunner(config=None, workspace=tmp_path)

    # No files
    h1 = runner._hash_deps()

    # Add requirements.txt
    (tmp_path / "requirements.txt").write_text("numpy\n")
    h2 = runner._hash_deps()
    assert h1 != h2

    # Modify requirements.txt
    (tmp_path / "requirements.txt").write_text("numpy\npandas\n")
    h3 = runner._hash_deps()
    assert h2 != h3


def test_hash_deps_stable(tmp_path):
    runner = SandboxRunner(config=None, workspace=tmp_path)
    (tmp_path / "requirements.txt").write_text("torch\n")
    assert runner._hash_deps() == runner._hash_deps()


# --- execute_with_repeat ---


def test_execute_with_repeat_docker(tmp_path):
    cfg = SandboxConfig(backend="docker")
    runner = SandboxRunner(config=cfg, workspace=tmp_path)
    ok_result = RunResult(exit_code=0, timed_out=False)

    with patch.object(runner, "execute", return_value=ok_result):
        with patch.object(runner, "parse_metric", side_effect=[1.0, 2.0, 3.0]):
            result, metric = runner.execute_with_repeat(
                "train", "eval", "acc", repeat=3, aggregation="median", timeout=30,
            )
            assert metric == 2.0


def test_execute_with_repeat_mean(tmp_path):
    runner = SandboxRunner(config=None, workspace=tmp_path)
    ok_result = RunResult(exit_code=0, timed_out=False)

    with patch.object(runner, "execute", return_value=ok_result):
        with patch.object(runner, "parse_metric", side_effect=[1.0, 2.0, 3.0]):
            result, metric = runner.execute_with_repeat(
                "train", "eval", "acc", repeat=3, aggregation="mean", timeout=30,
            )
            assert metric == 2.0


def test_execute_with_repeat_early_failure(tmp_path):
    runner = SandboxRunner(config=None, workspace=tmp_path)
    fail_result = RunResult(exit_code=1, timed_out=False, stderr_tail="error")

    with patch.object(runner, "execute", return_value=fail_result):
        result, metric = runner.execute_with_repeat(
            "train", "eval", "acc", repeat=3, aggregation="median", timeout=30,
        )
        assert metric is None
        assert result.exit_code == 1


def test_docker_shadows_env_file(tmp_path):
    """Docker run args should include shadow mount for .env if it exists."""
    import unittest.mock as mock
    from crucible.config import SandboxConfig
    from crucible.sandbox import SandboxRunner

    # Create a .env file in workspace
    (tmp_path / ".env").write_text("SECRET=abc123")

    config = SandboxConfig(backend="docker", base_image="python:3.11-slim")
    runner = SandboxRunner(config=config, workspace=tmp_path)

    with patch.object(runner, "_ensure_image", return_value="crucible-test:latest"):
        with mock.patch("crucible.sandbox.subprocess.Popen") as mock_popen:
            mock_proc = mock.MagicMock()
            mock_proc.communicate.return_value = ("", "")
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            runner._docker_run("echo test", 30)

            args = mock_popen.call_args[0][0]
            args_str = " ".join(str(a) for a in args)
            assert "/dev/null:/workspace/.env:ro" in args_str
