# Design: Persistent Artifacts Support

**Date**: 2026-03-16
**Status**: Approved

## Purpose

Allow crucible experiments to persist files (model weights, training data,
checkpoints) across iterations. Currently, `git reset --hard` on discard
destroys all untracked files, making ML training workflows impossible.

## Requirements

- Artifacts survive git revert/discard — not version-controlled
- Configurable per-project via `config.yaml`
- Works with both native and Docker sandbox execution
- Agent is informed of artifacts directories via context prompt

## Config Format

New optional field in `files` section:

```yaml
files:
  editable:
    - "strategy.py"
  readonly:
    - "game.py"
  hidden:
    - "evaluate.py"
  artifacts:
    - "artifacts/"
    - "checkpoints/"
```

- `artifacts` is optional, defaults to `[]`
- Values are path strings (directories end with `/`)
- These paths are automatically added to `.gitignore`

## Module Changes

### config.py

`FilesConfig` dataclass gets new field:

```python
@dataclass
class FilesConfig:
    editable: list[str] = field(default_factory=list)
    readonly: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)  # NEW
```

### orchestrator.py

**init() phase:**
- For each path in `config.files.artifacts`:
  - `mkdir -p` the directory
  - Add to `.gitignore` (same logic as `run.log` and `results-*.tsv`)

**revert phase (discard):**
- Current: `git reset --hard` + `git clean -fd`
- New: `git clean -fd` adds `--exclude=<path>` for each artifacts path
- Artifacts directories are NOT deleted on discard

### context.py

`_section_state()` adds artifacts info when configured:

```
Persistent directories (survive across iterations, not version-controlled):
  - artifacts/
Files in these directories are NOT affected by revert. Use them to store
model weights, training data, or other artifacts that should persist.
```

### sandbox.py

`_docker_run()` adds rw volume mounts for artifacts paths,
alongside existing editable file mounts:

```python
for artifact_path in artifacts:
    abs_path = workspace / artifact_path
    abs_path.mkdir(parents=True, exist_ok=True)
    volumes.append(f"-v {abs_path}:{container_workspace}/{artifact_path}:rw")
```

### validator.py

`validate_project()` checks:
- Artifacts paths don't overlap with editable/readonly/hidden paths
- Warn if artifacts paths exist but aren't directories

### guardrails.py

No changes needed. `_detect_modified_files()` uses `git ls-files` which
ignores .gitignored paths. Artifacts won't trigger guardrail violations.

## Behavior Summary

| Event | Artifacts behavior |
|-------|-------------------|
| `crucible init` | mkdir + add to .gitignore |
| Iteration keep | Untouched (not in git) |
| Iteration discard | Untouched (excluded from git clean) |
| Docker execution | Mounted as rw volumes |
| Native execution | Just a directory on disk |
| Agent context | Informed via _section_state() |

## Impact on optimize-2048

Add to config:

```yaml
files:
  artifacts:
    - "artifacts/"
```

Agent can then choose to:
1. Write training code in `strategy.py`
2. Store model weights in `artifacts/model.pkl`
3. Next iteration: load existing weights, continue training or use for inference

## Non-Goals

- No versioning of artifacts (use git-lfs if needed)
- No size limits on artifacts directory
- No automatic cleanup of old artifacts
