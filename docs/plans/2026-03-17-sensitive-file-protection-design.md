# Sensitive File Protection Design

**Date:** 2026-03-17

## Problem

Crucible agents (Claude Code via Agent SDK) can use the `Read` tool to read any file in the workspace, including `.env`, `.ssh/` keys, and other credential files. In threat model C (untrusted agent, possible jailbreak), this creates a real exfiltration path:

1. Agent reads `.env` (API key)
2. Agent embeds key in generated code
3. Evaluation phase runs with network access
4. Key exfiltrated to external server

## Decision

**Approach B: PreToolUse hook-level blocking + Docker shadow mount**

Extend the existing `_make_file_hooks()` in `claude_code.py` to block Read access to files matching sensitive patterns. Add Docker shadow mount as defense-in-depth.

Symlink resolution was considered but not adopted — the agent's tools cannot create symlinks, making this attack vector nearly impossible.

## Design

### Pattern Classification

Two pattern types for matching:

**Sensitive directory patterns** (block if any path component matches exactly):
```python
SENSITIVE_DIR_PATTERNS = {".ssh", ".aws", ".gnupg", ".kube", ".azure", ".gcloud"}
```

**Sensitive file prefix patterns** (block if filename starts with pattern):
```python
SENSITIVE_FILE_PREFIXES = {".env"}  # catches .env, .env.local, .env.production
```

Patterns are hardcoded — NOT configurable via config.yaml. This prevents an agent from modifying its own permission set.

### Hook Change

Current behavior:
- Read/Glob/Grep: only block hidden files
- Edit/Write: block hidden + non-editable

New behavior:
- Read/Glob/Grep: block hidden files **+ sensitive patterns**
- Edit/Write: unchanged

Error message shown to agent:
```
Access denied: <path> matches sensitive file pattern.
Crucible does not allow reading credential or key files.
```

### Docker Shadow Mount

In `SandboxRunner._docker_run()`, shadow common `.env` variants with `/dev/null` before workspace is mounted:

```python
for name in (".env", ".env.local", ".env.production", ".env.staging"):
    if (self.workspace / name).exists():
        cmd.extend(["-v", f"/dev/null:/workspace/{name}:ro"])
```

This is defense-in-depth — the hook already blocks Read, but the shadow ensures even a hook bypass can't expose the file.

### Scope

- Only applies during agent SDK session (PreToolUse hook)
- Evaluation subprocess is NOT affected — experiment code can still read `.env` legitimately
- Glob pattern results are filtered (paths matching patterns are excluded from results)

## Testing

Three new async tests in `tests/test_agents.py`:

1. `test_hook_blocks_env_read` — `.env` → deny
2. `test_hook_blocks_ssh_dir_read` — `.ssh/config` → deny
3. `test_hook_allows_normal_read` — `solution.py` → allow

## Files Changed

- `src/crucible/agents/claude_code.py` — extend `_make_file_hooks()` and `pre_tool_use_hook`
- `src/crucible/sandbox.py` — add shadow mounts in `_docker_run()`
- `tests/test_agents.py` — add 3 tests
