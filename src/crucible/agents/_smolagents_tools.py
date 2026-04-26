"""smolagents Tool subclasses — M2 PR 13.

Thin wrappers around `crucible.agents._path_acl.safe_*` helpers. The
real ACL/normalization logic lives in `_path_acl` (testable without
smolagents); this module only adapts those helpers to the
`smolagents.Tool` framework.

Per spec §INV-3: the default-safe-mode tool registry is exactly these
five tools — `read_file`, `write_file`, `edit_file`, `glob`, `grep`.
`run_python` / `run_shell` / `eval_code` / generic `execute` /
CodeAct executor MUST NOT be in this list (reviewer round 1 pin).

Each tool's `forward()` catches `ToolDenied` from the underlying
helper and returns a structured error string the smolagents agent
loop can recover from (it isn't a Python exception in the agent's
context).
"""

from __future__ import annotations

from pathlib import Path

from smolagents import Tool

from crucible.agents._path_acl import (
    ToolDenied,
    safe_edit,
    safe_glob,
    safe_grep,
    safe_read,
    safe_write,
)
from crucible.security.cheat_resistance_policy import CheatResistancePolicy


def _format_denied(exc: ToolDenied) -> str:
    """Sanitised denial message returned to the smolagents agent.

    Returning a string (rather than raising) lets the agent loop see
    the error in its tool-output channel and choose another approach.
    """
    return f"[denied] {exc}"


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read the contents of a workspace file. Allowed for `editable` and "
        "`readonly` files. Hidden / unlisted files (e.g. the evaluation "
        "harness) are not visible. Returns file contents as a UTF-8 string, "
        "truncated at 256 KiB."
    )
    inputs = {
        "path": {
            "type": "string",
            "description": "Workspace-relative file path.",
        }
    }
    output_type = "string"

    def __init__(
        self, *, policy: CheatResistancePolicy, workspace: Path
    ) -> None:
        super().__init__()
        self._policy = policy
        self._workspace = workspace

    def forward(self, path: str) -> str:
        try:
            return safe_read(path, policy=self._policy, workspace=self._workspace)
        except ToolDenied as exc:
            return _format_denied(exc)


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Overwrite (or create) a workspace file. Allowed only for `editable` "
        "files. Hidden / readonly / unlisted files are denied. Use this for "
        "full-file rewrites; use `edit_file` for targeted search/replace."
    )
    inputs = {
        "path": {
            "type": "string",
            "description": "Workspace-relative file path.",
        },
        "content": {
            "type": "string",
            "description": "Full file content to write.",
        },
    }
    output_type = "string"

    def __init__(
        self, *, policy: CheatResistancePolicy, workspace: Path
    ) -> None:
        super().__init__()
        self._policy = policy
        self._workspace = workspace

    def forward(self, path: str, content: str) -> str:
        try:
            n = safe_write(
                path, content, policy=self._policy, workspace=self._workspace
            )
            return f"wrote {n} bytes to {path}"
        except ToolDenied as exc:
            return _format_denied(exc)


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Targeted search/replace in an editable file. Replaces the FIRST "
        "and only occurrence of `old` with `new`. The `old` substring "
        "must appear exactly once — if zero or multiple matches exist, "
        "the edit is rejected (use `write_file` for full-file rewrites or "
        "narrow the snippet)."
    )
    inputs = {
        "path": {
            "type": "string",
            "description": "Workspace-relative file path.",
        },
        "old": {
            "type": "string",
            "description": "Substring to replace (must be unique in file).",
        },
        "new": {
            "type": "string",
            "description": "Replacement substring.",
        },
    }
    output_type = "string"

    def __init__(
        self, *, policy: CheatResistancePolicy, workspace: Path
    ) -> None:
        super().__init__()
        self._policy = policy
        self._workspace = workspace

    def forward(self, path: str, old: str, new: str) -> str:
        try:
            n = safe_edit(
                path, old, new, policy=self._policy, workspace=self._workspace
            )
            return f"edited {path} ({n} bytes)"
        except ToolDenied as exc:
            return _format_denied(exc)


class GlobTool(Tool):
    name = "glob"
    description = (
        "List workspace files matching a glob pattern. Hidden and unlisted "
        "files are silently filtered out — the result reflects only what "
        "the agent is allowed to see."
    )
    inputs = {
        "pattern": {
            "type": "string",
            "description": "Glob pattern (e.g. `**/*.py`). Workspace-rooted.",
        }
    }
    output_type = "string"

    def __init__(
        self, *, policy: CheatResistancePolicy, workspace: Path
    ) -> None:
        super().__init__()
        self._policy = policy
        self._workspace = workspace

    def forward(self, pattern: str) -> str:
        try:
            matches = safe_glob(
                pattern, policy=self._policy, workspace=self._workspace
            )
            if not matches:
                return "(no matches)"
            return "\n".join(matches)
        except ToolDenied as exc:
            return _format_denied(exc)


class GrepTool(Tool):
    name = "grep"
    description = (
        "Search for a regex pattern across visible workspace files. By "
        "default, searches all editable + readonly files. Optionally "
        "restrict to specific files (hidden/unlisted are skipped silently). "
        "Returns up to 200 `path:line: text` matches."
    )
    inputs = {
        "pattern": {
            "type": "string",
            "description": "Python regex pattern.",
        },
        "files": {
            "type": "string",
            "description": (
                "Optional comma-separated list of workspace-relative paths "
                "to restrict the search. Empty string = all visible files."
            ),
            "nullable": True,
        },
    }
    output_type = "string"

    def __init__(
        self, *, policy: CheatResistancePolicy, workspace: Path
    ) -> None:
        super().__init__()
        self._policy = policy
        self._workspace = workspace

    def forward(self, pattern: str, files: str = "") -> str:
        try:
            files_list = (
                [f.strip() for f in files.split(",") if f.strip()]
                if files else None
            )
            matches = safe_grep(
                pattern,
                files_list,
                policy=self._policy,
                workspace=self._workspace,
            )
            if not matches:
                return "(no matches)"
            return "\n".join(matches)
        except ToolDenied as exc:
            return _format_denied(exc)


# Public registry — used by the backend to construct tools for an agent.
# Forbidden tools (`run_python`, `run_shell`, `eval_code`, generic `execute`,
# CodeAct executor) are absent BY CONSTRUCTION — they have no entry here
# and cannot be added without editing this file. (Reviewer round 1 pin.)
DEFAULT_SAFE_TOOLS = (
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    GlobTool,
    GrepTool,
)


def build_default_tools(
    *, policy: CheatResistancePolicy, workspace: Path
) -> list[Tool]:
    """Construct the default-safe-mode tool list for a SmolagentsBackend."""
    return [
        cls(policy=policy, workspace=workspace) for cls in DEFAULT_SAFE_TOOLS
    ]


# Tool name → class registry (handy for tests asserting absence).
TOOL_NAMES_DEFAULT = tuple(cls.name for cls in DEFAULT_SAFE_TOOLS)
FORBIDDEN_TOOL_NAMES = (
    "run_python",
    "run_shell",
    "eval_code",
    "execute",
    "python_interpreter",
    "code_act",
)
