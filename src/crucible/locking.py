"""Worktree-level concurrency primitive — M2 PR 14.

Spec ref: §4 "One attempt per worktree at any time (worktree-level
lock)". This module provides the kernel-flock-backed `WorktreeMutex`
that future parallel BFTS workers will use to claim a worktree
without stepping on each other.

**Design contract** (reviewer round 1):

  - The kernel `flock(LOCK_EX | LOCK_NB)` IS the authority. The JSON
    sentinel file is metadata only — useful for diagnostics ("who
    holds it?") and for the cleanup CLI to identify orphan
    worktrees, but NEVER used to bypass a failed acquisition.
  - Never unlink/recreate the lock file based on sentinel content
    while it might be held — that creates split-brain (both A and B
    can hold flock on different inodes pointing to the "same" path).
  - Stale sentinel after acquisition: the lock acquired the file
    cleanly, the previous owner's metadata is just leftover. We
    overwrite with our own owner info and log a recovery line.
  - The sentinel lives at `<workspace>/logs/locks/<id>.lock`, a
    platform-owned area not configurable by the user / agent. No
    reliance on `files.hidden` config.

Windows: `fcntl` unavailable. Calling `acquire()` raises
`RuntimeError` mirroring `TrialLedger`'s behaviour (spec §11 INV-4).
v1.0 documents Windows as unsupported for concurrent ledger writes;
M2+ may add `msvcrt.locking`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import socket
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_WINDOWS = platform.system() == "Windows"
if not _WINDOWS:
    import fcntl


# Module-level default: enough for normal contention; the orchestrator
# can pass a longer / shorter override via constructor.
DEFAULT_TIMEOUT_SECONDS = 30.0

# Polling cadence while waiting for an unheld lock.
_POLL_INTERVAL_SECONDS = 0.1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WorktreeLocked(RuntimeError):
    """Raised when `WorktreeMutex.acquire()` times out waiting for the lock.

    Carries the holding owner metadata (best-effort, parsed from the
    sentinel) so the caller can produce a useful "lock held by PID X
    on host H since T" error message.
    """

    def __init__(
        self,
        lock_path: Path,
        timeout: float,
        owner: Optional["LockOwner"],
    ) -> None:
        owner_str = (
            f"pid={owner.pid} host={owner.host} since {owner.claimed_at}"
            if owner is not None
            else "unknown owner"
        )
        super().__init__(
            f"worktree lock {lock_path} held by {owner_str}; "
            f"timed out after {timeout:.1f}s"
        )
        self.lock_path = lock_path
        self.owner = owner


# ---------------------------------------------------------------------------
# LockOwner — sentinel metadata
# ---------------------------------------------------------------------------


@dataclass
class LockOwner:
    """Metadata snapshot stamped into the sentinel file under flock.

    `process_create_time` is best-effort: if `psutil` is available we
    record `Process.create_time()` for PID-reuse defence; otherwise
    it stays None and PID-only matching is used.
    """
    pid: int
    host: str
    process_create_time: Optional[float]
    claimed_at: str  # ISO-8601 UTC

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> Optional["LockOwner"]:
        try:
            data = json.loads(raw)
            return cls(
                pid=int(data["pid"]),
                host=str(data["host"]),
                process_create_time=(
                    float(data["process_create_time"])
                    if data.get("process_create_time") is not None
                    else None
                ),
                claimed_at=str(data["claimed_at"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None


def _read_owner(lock_path: Path) -> Optional[LockOwner]:
    """Best-effort read of sentinel metadata. Returns None on any error."""
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    return LockOwner.from_json(raw)


def _current_owner() -> LockOwner:
    """Snapshot the calling process's identity for the sentinel."""
    pid = os.getpid()
    host = socket.gethostname()
    create_time: Optional[float] = None
    try:
        import psutil  # optional dependency; not required
        create_time = psutil.Process(pid).create_time()
    except Exception:
        create_time = None
    return LockOwner(
        pid=pid,
        host=host,
        process_create_time=create_time,
        claimed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


# ---------------------------------------------------------------------------
# Stale-owner classification (used by cleanup, NOT by acquire)
# ---------------------------------------------------------------------------


def is_owner_alive(owner: LockOwner) -> bool:
    """Best-effort check whether `owner.pid` is the same process that
    wrote the sentinel.

    Reviewer round 1: this is for diagnostic / cleanup eligibility ONLY.
    `WorktreeMutex.acquire()` MUST NOT use it to bypass a failed flock —
    flock is the authority.

    Returns True if:
      - pid is currently running on the same host AND
      - if `process_create_time` is recorded, it matches the running
        process's create_time (defends against PID reuse)

    Returns False if:
      - process is dead
      - process exists but create_time differs (PID reused since the
        sentinel was written)
      - host doesn't match (we can't reason about other hosts)
    """
    # Cross-host: can't introspect, treat as alive (conservative).
    if owner.host != socket.gethostname():
        return True

    try:
        os.kill(owner.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but isn't ours; conservative: alive.
        return True
    except OSError:
        return True

    if owner.process_create_time is None:
        return True  # PID-only — accept

    try:
        import psutil
        live_create_time = psutil.Process(owner.pid).create_time()
    except Exception:
        return True
    # Allow tiny float drift across platforms.
    return abs(live_create_time - owner.process_create_time) < 1.0


# ---------------------------------------------------------------------------
# WorktreeMutex
# ---------------------------------------------------------------------------


class WorktreeMutex:
    """Context manager that holds an exclusive lock over a worktree.

    Usage:
        with WorktreeMutex(workspace) as mutex:
            # exclusive use of `workspace` for this attempt
            ...
        # released on exit (normal or exception)

    The sentinel file is created at
    `<workspace>/logs/locks/<safe_id>.lock` where `safe_id` is the
    workspace's basename (path-sanitised). The `logs/` subtree is
    platform-owned and not exposed to the agent.

    Acquisition:
      - `flock(LOCK_EX | LOCK_NB)` is attempted; on success the file
        is overwritten with current `LockOwner` JSON.
      - On `BlockingIOError` (already held), poll up to `timeout`
        before raising `WorktreeLocked`.
      - We read the sentinel only to populate the error message.
    """

    def __init__(
        self,
        workspace: Path | str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        lock_id: Optional[str] = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.timeout = float(timeout)
        # Lock file ID: sanitised workspace name; caller can override.
        sanitised = (lock_id or self.workspace.name or "default").replace("/", "_")
        self.lock_path = self.workspace / "logs" / "locks" / f"{sanitised}.lock"
        self._fh = None
        self._held = False

    def __enter__(self) -> "WorktreeMutex":
        self.acquire(timeout=self.timeout)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def acquire(self, *, timeout: Optional[float] = None) -> None:
        if _WINDOWS:
            raise RuntimeError(
                "WorktreeMutex requires POSIX fcntl; Windows is unsupported "
                "in v1.0 (matches TrialLedger). Use single-process mode only."
            )
        if self._held:
            raise RuntimeError("WorktreeMutex already held by this instance")

        timeout = timeout if timeout is not None else self.timeout
        deadline = time.monotonic() + timeout

        # Ensure the lock file's parent exists.
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Open in append+read mode so the inode persists across attempts.
        # We hold this fh for the lifetime of the lock; closing releases
        # the kernel flock.
        self._fh = open(self.lock_path, "a+", encoding="utf-8")

        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                # SUCCESS: we own the kernel lock. Now safely overwrite
                # the sentinel metadata. We do NOT unlink — same inode,
                # truncate-and-write under flock.
                self._fh.seek(0)
                self._fh.truncate()
                self._fh.write(_current_owner().to_json() + "\n")
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._held = True
                return
            except BlockingIOError:
                # Held by someone else. Read sentinel for diagnostics
                # ONLY — never act on it.
                if time.monotonic() >= deadline:
                    owner = _read_owner(self.lock_path)
                    self._fh.close()
                    self._fh = None
                    raise WorktreeLocked(self.lock_path, timeout, owner)
                time.sleep(_POLL_INTERVAL_SECONDS)

    def release(self) -> None:
        if not self._held or self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
            self._held = False

    @property
    def held(self) -> bool:
        return self._held


# ---------------------------------------------------------------------------
# Cleanup helpers — used by `crucible cleanup` CLI (PR 14 step 2)
# ---------------------------------------------------------------------------


@dataclass
class CleanupCandidate:
    """A worktree that may be eligible for cleanup."""
    worktree_path: Path
    lock_path: Optional[Path]
    owner: Optional[LockOwner]
    reason: str  # human-readable category: "orphan", "stale", "live", "no-lock-file"


def classify_worktree(worktree_path: Path) -> CleanupCandidate:
    """Inspect a candidate worktree and classify it for cleanup.

    Reviewer round 1: classification is METADATA-driven; the actual
    cleanup decision additionally requires successfully acquiring the
    kernel lock non-blocking (handled by `try_cleanup_worktree`).
    """
    workspace = Path(worktree_path).resolve()
    lock_path = workspace / "logs" / "locks" / f"{workspace.name}.lock"

    if not lock_path.exists():
        return CleanupCandidate(
            worktree_path=workspace,
            lock_path=None,
            owner=None,
            reason="no-lock-file",
        )

    owner = _read_owner(lock_path)
    if owner is None:
        return CleanupCandidate(
            worktree_path=workspace,
            lock_path=lock_path,
            owner=None,
            reason="orphan",  # malformed sentinel
        )

    if is_owner_alive(owner):
        return CleanupCandidate(
            worktree_path=workspace,
            lock_path=lock_path,
            owner=owner,
            reason="live",
        )
    return CleanupCandidate(
        worktree_path=workspace,
        lock_path=lock_path,
        owner=owner,
        reason="stale",
    )


def try_acquire_for_cleanup(
    workspace: Path | str, *, timeout: float = 0.0
) -> Optional[WorktreeMutex]:
    """Best-effort non-blocking lock acquisition for cleanup safety.

    Returns the held mutex (caller must release) on success, or None
    if the lock is busy. NEVER unlinks the lock file based on
    sentinel content — flock is the authority.
    """
    if _WINDOWS:
        return None
    mutex = WorktreeMutex(workspace, timeout=timeout)
    try:
        mutex.acquire(timeout=timeout)
        return mutex
    except WorktreeLocked:
        return None


@contextlib.contextmanager
def safe_cleanup_lock(workspace: Path | str):
    """Context manager: yields the mutex if cleanup is safe, else None.

    Usage:
        with safe_cleanup_lock(ws) as held:
            if held is None:
                # busy, skip
                continue
            ... # safe to delete
    """
    mutex = try_acquire_for_cleanup(workspace, timeout=0.0)
    try:
        yield mutex
    finally:
        if mutex is not None and mutex.held:
            mutex.release()
