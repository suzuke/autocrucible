"""Tests for `crucible.locking.WorktreeMutex` — M2 PR 14.

Reviewer round 1 minimum test set:
  1. Acquire / release / re-acquire from same process
  2. Subprocess contention: B times out while A holds; error includes
     owner metadata
  3. **No split-brain**: stale-looking sentinel + live flock-holder →
     B must NOT steal/unlink
  4. Stale-sentinel recovery: no live flock-holder → B acquires and
     overwrites metadata
  5. Cleanup: classify_worktree + safe_cleanup_lock semantics
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from crucible.locking import (
    LockOwner,
    WorktreeLocked,
    WorktreeMutex,
    _current_owner,
    _read_owner,
    classify_worktree,
    is_owner_alive,
    safe_cleanup_lock,
    try_acquire_for_cleanup,
)


# Skip the entire module on Windows — fcntl unavailable, matches
# TrialLedger's stance per spec §11 INV-4.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="WorktreeMutex requires POSIX fcntl",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _read_sentinel(workspace: Path) -> dict:
    lock_path = workspace / "logs" / "locks" / f"{workspace.name}.lock"
    raw = lock_path.read_text().strip()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Acquire / release / reacquire
# ---------------------------------------------------------------------------


def test_acquire_release_reacquire_same_process(workspace: Path):
    """Same process can acquire, release, then acquire again."""
    m1 = WorktreeMutex(workspace)
    m1.acquire()
    assert m1.held is True
    m1.release()
    assert m1.held is False

    m2 = WorktreeMutex(workspace)
    m2.acquire()
    assert m2.held is True
    m2.release()


def test_context_manager_releases_on_exit(workspace: Path):
    with WorktreeMutex(workspace) as m:
        assert m.held is True
    assert m.held is False


def test_context_manager_releases_on_exception(workspace: Path):
    with pytest.raises(RuntimeError, match="boom"):
        with WorktreeMutex(workspace) as m:
            assert m.held is True
            raise RuntimeError("boom")
    assert m.held is False
    # Lock is releasable: a fresh acquire succeeds.
    with WorktreeMutex(workspace, timeout=1.0):
        pass


def test_double_acquire_same_instance_is_error(workspace: Path):
    m = WorktreeMutex(workspace)
    m.acquire()
    try:
        with pytest.raises(RuntimeError, match="already held"):
            m.acquire()
    finally:
        m.release()


# ---------------------------------------------------------------------------
# Sentinel metadata is written under flock
# ---------------------------------------------------------------------------


def test_sentinel_written_with_owner_metadata(workspace: Path):
    with WorktreeMutex(workspace):
        data = _read_sentinel(workspace)
    assert data["pid"] == os.getpid()
    assert "host" in data
    assert "claimed_at" in data


def test_sentinel_overwritten_on_subsequent_acquire(workspace: Path):
    """Stale sentinel from previous owner is overwritten cleanly when
    the next acquire succeeds — no unlink/recreate needed."""
    # First holder writes sentinel
    with WorktreeMutex(workspace):
        first = _read_sentinel(workspace)
    # Sentinel persists across release (we only flock-unlock, don't unlink)
    lock_path = workspace / "logs" / "locks" / f"{workspace.name}.lock"
    assert lock_path.exists()
    assert _read_sentinel(workspace) == first

    # Manually write a "stale" sentinel as if from a dead process
    lock_path.write_text(
        json.dumps({
            "pid": 99999999,  # almost certainly dead
            "host": "old-host",
            "process_create_time": None,
            "claimed_at": "2020-01-01T00:00:00Z",
        }) + "\n"
    )

    # Acquire again → flock succeeds → sentinel is overwritten with NEW owner
    with WorktreeMutex(workspace):
        second = _read_sentinel(workspace)
    assert second["pid"] == os.getpid()
    assert second["pid"] != 99999999


# ---------------------------------------------------------------------------
# CRITICAL: no split-brain — stale-looking sentinel + live flock-holder
# (reviewer round 1 primary concern)
# ---------------------------------------------------------------------------


def test_no_split_brain_stale_sentinel_with_live_holder(workspace: Path, tmp_path: Path):
    """If process A holds the kernel flock and the sentinel JSON
    *looks* stale (e.g. wrong PID), process B must NOT steal/unlink/
    recreate the file. Both processes can't end up holding "the" lock.
    """
    # Spawn worker A that acquires lock, overwrites sentinel with stale-
    # LOOKING content, holds for 2.5s, then exits.
    helper = tmp_path / "_holder.py"
    helper.write_text(textwrap.dedent(f"""
        import json, sys, time
        sys.path.insert(0, "{Path(__file__).parent.parent / 'src'}")
        from crucible.locking import WorktreeMutex

        ws = "{workspace}"
        with WorktreeMutex(ws) as m:
            # Replace sentinel with stale-looking content (we ARE the
            # live holder, but the JSON pretends to be a long-dead PID).
            lock = m.lock_path
            lock.write_text(json.dumps({{
                "pid": 99999999,
                "host": "stale-host",
                "process_create_time": None,
                "claimed_at": "2020-01-01T00:00:00Z",
            }}) + "\\n")
            print("HELD", flush=True)
            time.sleep(2.5)
        print("RELEASED", flush=True)
    """))

    proc = subprocess.Popen(
        [sys.executable, str(helper)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # Wait for "HELD" so we know A holds the kernel lock
        line = proc.stdout.readline().strip()
        assert line == "HELD", f"helper did not acquire lock; output: {line}"

        # Now B (this process) tries to acquire with a short timeout.
        # The sentinel LOOKS stale (PID 99999999 is dead), but the
        # kernel flock IS held by A. B MUST get WorktreeLocked, NOT
        # steal-and-acquire.
        m = WorktreeMutex(workspace, timeout=0.5)
        with pytest.raises(WorktreeLocked) as ei:
            m.acquire()
        assert m.held is False
        # Owner from sentinel is reflected in the error for diagnostics
        assert ei.value.owner is not None
        assert ei.value.owner.pid == 99999999

        # Verify the lock file was NOT unlinked or replaced — same content
        lock_path = workspace / "logs" / "locks" / f"{workspace.name}.lock"
        data = json.loads(lock_path.read_text().strip())
        assert data["pid"] == 99999999, "B tampered with sentinel under live flock"
    finally:
        proc.wait(timeout=5)
        assert proc.returncode == 0


# ---------------------------------------------------------------------------
# Subprocess contention: B times out while A holds
# ---------------------------------------------------------------------------


def test_subprocess_contention_b_times_out(workspace: Path, tmp_path: Path):
    helper = tmp_path / "_holder.py"
    helper.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, "{Path(__file__).parent.parent / 'src'}")
        from crucible.locking import WorktreeMutex
        with WorktreeMutex("{workspace}") as m:
            print("HELD", flush=True)
            time.sleep(2.0)
    """))

    proc = subprocess.Popen(
        [sys.executable, str(helper)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "HELD"

        m = WorktreeMutex(workspace, timeout=0.3)
        t0 = time.monotonic()
        with pytest.raises(WorktreeLocked) as ei:
            m.acquire()
        elapsed = time.monotonic() - t0
        # Timeout was 0.3s; allow generous slack.
        assert 0.25 <= elapsed <= 1.5, f"unexpected elapsed: {elapsed}"

        # Error message contains owner pid + timeout info
        msg = str(ei.value)
        assert "timed out" in msg
        assert ei.value.owner is not None
        assert ei.value.owner.pid == proc.pid
    finally:
        proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Cleanup: classify + safe_cleanup_lock
# ---------------------------------------------------------------------------


def test_classify_no_lock_file(workspace: Path):
    cand = classify_worktree(workspace)
    assert cand.reason == "no-lock-file"
    assert cand.owner is None


def test_classify_live_owner(workspace: Path):
    with WorktreeMutex(workspace):
        cand = classify_worktree(workspace)
    # After release, the lock file persists with the previous owner's
    # metadata. Since the previous owner IS this process and we're
    # alive, classify says "live".
    assert cand.reason in ("live", "stale")  # "live" if same process
    if cand.reason == "live":
        assert cand.owner is not None
        assert cand.owner.pid == os.getpid()


def test_classify_orphan_malformed_sentinel(workspace: Path):
    # Create lock file with garbage so LockOwner.from_json returns None.
    lock_path = workspace / "logs" / "locks" / f"{workspace.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("not valid json")
    cand = classify_worktree(workspace)
    assert cand.reason == "orphan"


def test_classify_stale_dead_pid(workspace: Path):
    lock_path = workspace / "logs" / "locks" / f"{workspace.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({
        "pid": 99999999,
        "host": "localhost-not-this",  # cross-host fallback alive
        "process_create_time": None,
        "claimed_at": "2020-01-01T00:00:00Z",
    }) + "\n")
    cand = classify_worktree(workspace)
    # Cross-host means we conservatively classify as live; same-host
    # dead-pid would be "stale". Use same-host:
    import socket
    lock_path.write_text(json.dumps({
        "pid": 99999999,
        "host": socket.gethostname(),
        "process_create_time": None,
        "claimed_at": "2020-01-01T00:00:00Z",
    }) + "\n")
    cand = classify_worktree(workspace)
    assert cand.reason == "stale"


def test_safe_cleanup_lock_skips_busy(workspace: Path, tmp_path: Path):
    """Reviewer round 1: cleanup must skip a worktree whose lock is
    actually held — even if sentinel looks stale."""
    helper = tmp_path / "_holder.py"
    helper.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, "{Path(__file__).parent.parent / 'src'}")
        from crucible.locking import WorktreeMutex
        with WorktreeMutex("{workspace}") as m:
            print("HELD", flush=True)
            time.sleep(1.5)
    """))

    proc = subprocess.Popen(
        [sys.executable, str(helper)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "HELD"
        with safe_cleanup_lock(workspace) as held:
            # Must NOT be able to acquire — busy.
            assert held is None
    finally:
        proc.wait(timeout=5)


def test_safe_cleanup_lock_succeeds_when_unheld(workspace: Path):
    # Pre-write a stale sentinel
    lock_path = workspace / "logs" / "locks" / f"{workspace.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({
        "pid": 99999999,
        "host": "old-host",
        "process_create_time": None,
        "claimed_at": "2020-01-01T00:00:00Z",
    }) + "\n")

    with safe_cleanup_lock(workspace) as held:
        # Lock is unheld so cleanup should be able to acquire.
        assert held is not None
        assert held.held is True


# ---------------------------------------------------------------------------
# is_owner_alive defensive check
# ---------------------------------------------------------------------------


def test_is_owner_alive_dead_pid_returns_false():
    owner = LockOwner(
        pid=99999999,
        host=__import__("socket").gethostname(),
        process_create_time=None,
        claimed_at="2020-01-01T00:00:00Z",
    )
    assert is_owner_alive(owner) is False


def test_is_owner_alive_self_returns_true():
    owner = _current_owner()
    assert is_owner_alive(owner) is True


def test_is_owner_alive_cross_host_returns_true_conservative():
    """Cross-host: we can't introspect → conservatively treat as alive
    so cleanup never deletes another machine's worktree."""
    owner = LockOwner(
        pid=os.getpid(),
        host="some-other-host-not-mine",
        process_create_time=None,
        claimed_at="2020-01-01T00:00:00Z",
    )
    assert is_owner_alive(owner) is True
