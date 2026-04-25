"""ACL-enforced filesystem helpers for backend tools — M2 PR 13.

These functions are the "Crucible-owned" filesystem-access layer that
backends (smolagents, future SubscriptionCLIBackend, etc.) call from
their tool implementations. They:

  - Normalize paths against the workspace root (no traversal escapes).
  - Delegate ACL classification to `CheatResistancePolicy` (SSOT).
  - Sanitize error messages so hidden-file existence and eval-command
    detail are NOT leaked to the agent (reviewer round 1 pin).
  - Are independent of any specific agent framework — testable without
    `smolagents` installed.

Spec ref: §INV-3 narrow tool schema; §2.1 default-safe-mode tools.

Reading: allowed for `editable | readonly`. Denied for `hidden |
unlisted`. Writing: allowed for `editable` only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from crucible.security.cheat_resistance_policy import (
    CheatResistancePolicy,
    PolicyViolation,
)


# Cap on bytes returned from a single read_file call. Prevents the agent
# from exfiltrating very large files via a single tool call.
DEFAULT_READ_LIMIT_BYTES = 256 * 1024  # 256 KiB

# Cap on lines returned from a single grep call.
DEFAULT_GREP_MATCH_LIMIT = 200


class ToolDenied(RuntimeError):
    """Raised when a tool call is rejected by ACL or input validation.

    The message is SAFE to surface to the agent — never includes hidden
    file existence hints, absolute host paths, or eval-command details.
    Workspace-relative paths are OK; absolute paths are not.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_inside_workspace(path_arg: str, workspace: Path) -> Path:
    """Resolve `path_arg` to an absolute path inside `workspace`.

    Raises ToolDenied if the resolved path escapes the workspace via
    `..`, absolute path, or symlink. Returns the canonical resolved
    path (may not exist yet — caller is responsible for that check).
    """
    if not isinstance(path_arg, str) or not path_arg:
        raise ToolDenied("path argument must be a non-empty string")
    p = Path(path_arg)
    workspace_abs = workspace.resolve()
    if p.is_absolute():
        candidate = p
    else:
        candidate = (workspace_abs / p)
    # Resolve symlinks; this also normalizes ".." segments.
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        # `resolve(strict=False)` should not raise on missing components,
        # but defensively catch and reject.
        raise ToolDenied(f"path could not be normalized: {path_arg!r}")
    # The resolved path MUST be inside the workspace tree.
    try:
        resolved.relative_to(workspace_abs)
    except ValueError:
        raise ToolDenied(
            f"path escapes workspace: {path_arg!r}"
        )
    return resolved


def _safe_relpath(path: Path, workspace: Path) -> str:
    """Return a workspace-relative POSIX string for use in error messages."""
    try:
        return str(path.relative_to(workspace.resolve())).replace("\\", "/")
    except ValueError:
        return path.name  # last-resort fallback (shouldn't happen post-resolve)


def _classification_safe_error(
    classification: str,
    workspace_relative_path: str,
    operation: str,
) -> ToolDenied:
    """Produce a ToolDenied with a sanitized message.

    Reviewer round 1 pin: tool errors must NOT leak hidden path contents
    or eval-command details. We reveal only:
      - the operation attempted (`read` / `write`)
      - the requested path (already workspace-relative)
      - the classification name (`hidden`, `unlisted`, `readonly`)

    For `hidden`, we deliberately use a generic phrasing ("not visible
    to the agent") that does not reveal whether the file exists.
    """
    if classification == "hidden":
        msg = (
            f"{operation} denied: {workspace_relative_path!r} is not visible "
            f"to the agent."
        )
    elif classification == "unlisted":
        msg = (
            f"{operation} denied: {workspace_relative_path!r} is outside the "
            f"agent's working set. Add it to `files.editable` or "
            f"`files.readonly` in config to access."
        )
    elif classification == "readonly":
        msg = (
            f"{operation} denied: {workspace_relative_path!r} is read-only. "
            f"Editable files only."
        )
    else:
        msg = f"{operation} denied: {workspace_relative_path!r}."
    return ToolDenied(msg)


# ---------------------------------------------------------------------------
# Public API — used by smolagents tools (and any future backend's tools)
# ---------------------------------------------------------------------------


def safe_read(
    path_arg: str,
    *,
    policy: CheatResistancePolicy,
    workspace: Path,
    limit_bytes: int = DEFAULT_READ_LIMIT_BYTES,
) -> str:
    """Read a file's contents as text, enforcing read-side ACL.

    Allowed for `editable | readonly`. Denied for `hidden | unlisted`.
    Returns up to `limit_bytes` bytes decoded as UTF-8 (errors='replace').
    """
    resolved = _resolve_inside_workspace(path_arg, workspace)
    try:
        policy.assert_visible(resolved)
    except PolicyViolation as exc:
        raise _classification_safe_error(
            exc.classification,
            _safe_relpath(resolved, workspace),
            "read",
        ) from None  # don't chain — original exception leaks abs path

    if not resolved.exists() or not resolved.is_file():
        raise ToolDenied(f"file not found: {_safe_relpath(resolved, workspace)!r}")

    raw = resolved.read_bytes()
    if len(raw) > limit_bytes:
        raw = raw[:limit_bytes]
        truncated_note = (
            f"\n[... truncated at {limit_bytes} bytes; "
            f"file is {resolved.stat().st_size} bytes total]"
        )
    else:
        truncated_note = ""
    return raw.decode("utf-8", errors="replace") + truncated_note


def safe_write(
    path_arg: str,
    content: str,
    *,
    policy: CheatResistancePolicy,
    workspace: Path,
) -> int:
    """Overwrite or create a file, enforcing write-side ACL.

    Allowed for `editable` only. Denied for everything else. Returns
    the number of bytes written.
    """
    if not isinstance(content, str):
        raise ToolDenied("content argument must be a string")
    resolved = _resolve_inside_workspace(path_arg, workspace)
    try:
        policy.assert_writable(resolved)
    except PolicyViolation as exc:
        raise _classification_safe_error(
            exc.classification,
            _safe_relpath(resolved, workspace),
            "write",
        ) from None

    encoded = content.encode("utf-8")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(encoded)
    return len(encoded)


def safe_edit(
    path_arg: str,
    old: str,
    new: str,
    *,
    policy: CheatResistancePolicy,
    workspace: Path,
) -> int:
    """Search/replace edit on an editable file (reviewer round 1 Q4).

    Reads existing contents, replaces FIRST occurrence of `old` with
    `new`, writes back. Both ACL checks (visible + writable) are
    required since edit_file does both reads and writes.

    Returns the number of bytes written. Raises ToolDenied if the
    target string isn't found exactly once.
    """
    if not isinstance(old, str) or not isinstance(new, str):
        raise ToolDenied("`old` and `new` arguments must be strings")
    if not old:
        raise ToolDenied("`old` argument must not be empty")
    resolved = _resolve_inside_workspace(path_arg, workspace)
    try:
        policy.assert_visible(resolved)
        policy.assert_writable(resolved)
    except PolicyViolation as exc:
        raise _classification_safe_error(
            exc.classification,
            _safe_relpath(resolved, workspace),
            "edit",
        ) from None

    if not resolved.exists() or not resolved.is_file():
        raise ToolDenied(f"file not found: {_safe_relpath(resolved, workspace)!r}")

    text = resolved.read_text(encoding="utf-8", errors="replace")
    occurrences = text.count(old)
    if occurrences == 0:
        raise ToolDenied(
            f"edit denied: `old` substring not found in "
            f"{_safe_relpath(resolved, workspace)!r}"
        )
    if occurrences > 1:
        raise ToolDenied(
            f"edit denied: `old` substring is ambiguous "
            f"({occurrences} occurrences) in "
            f"{_safe_relpath(resolved, workspace)!r}; provide a more "
            f"specific snippet or use write_file for full-file overwrite."
        )
    new_text = text.replace(old, new, 1)
    encoded = new_text.encode("utf-8")
    resolved.write_bytes(encoded)
    return len(encoded)


def safe_glob(
    pattern: str,
    *,
    policy: CheatResistancePolicy,
    workspace: Path,
    limit: int = 100,
) -> list[str]:
    """Workspace-rooted glob, filtered to visible paths only.

    Pattern is resolved relative to `workspace.resolve()`. Returns
    workspace-relative POSIX paths. Hidden / unlisted matches are
    silently dropped — the agent must not learn that they exist.
    """
    if not isinstance(pattern, str) or not pattern:
        raise ToolDenied("pattern argument must be a non-empty string")
    if "/.." in pattern or pattern.startswith(".."):
        raise ToolDenied("pattern must not include parent-dir traversal")
    # Reviewer round 2 F2: `Path.glob()` raises NotImplementedError on
    # absolute / drive-rooted patterns, which would escape the
    # sanitized-denial channel and bubble as a backend exception.
    # Reject explicitly here — patterns must be workspace-relative.
    if Path(pattern).is_absolute() or pattern.startswith(("/", "\\")):
        raise ToolDenied("pattern must be workspace-relative (no absolute paths)")
    workspace_abs = workspace.resolve()
    matches: list[str] = []
    # Path.glob() with `**` pattern is expensive; we accept it as
    # documented. Cap result size to `limit`.
    for match in workspace_abs.glob(pattern):
        try:
            classification = policy.classify(match)
        except Exception:
            continue
        if classification in ("hidden", "unlisted"):
            continue
        try:
            rel = str(match.relative_to(workspace_abs)).replace("\\", "/")
        except ValueError:
            continue
        matches.append(rel)
        if len(matches) >= limit:
            break
    matches.sort()
    return matches


def safe_grep(
    pattern: str,
    files: Iterable[str] | None = None,
    *,
    policy: CheatResistancePolicy,
    workspace: Path,
    limit: int = DEFAULT_GREP_MATCH_LIMIT,
) -> list[str]:
    """Workspace-rooted grep, ACL-filtered.

    Searches `files` (relative paths) or all editable+readonly files if
    `files` is None. Returns formatted "{path}:{line_no}: {line}"
    matches, capped at `limit`. Hidden / unlisted files are skipped.
    """
    if not isinstance(pattern, str) or not pattern:
        raise ToolDenied("pattern argument must be a non-empty string")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolDenied(f"invalid regex: {exc}")

    workspace_abs = workspace.resolve()
    if files is None:
        # Default scope: all editable + readonly files.
        targets = list(policy.editable) + list(policy.readonly)
    else:
        targets = []
        for f in files:
            try:
                resolved = _resolve_inside_workspace(f, workspace)
            except ToolDenied:
                continue
            classification = policy.classify(resolved)
            if classification in ("hidden", "unlisted"):
                continue
            targets.append(resolved)

    out: list[str] = []
    for target in targets:
        if not target.is_file():
            continue
        try:
            with target.open("r", encoding="utf-8", errors="replace") as fp:
                for lineno, line in enumerate(fp, start=1):
                    if regex.search(line):
                        try:
                            rel = str(target.relative_to(workspace_abs)).replace("\\", "/")
                        except ValueError:
                            rel = target.name
                        out.append(f"{rel}:{lineno}: {line.rstrip()}")
                        if len(out) >= limit:
                            return out
        except OSError:
            continue
    return out
