---
name: llm-scaffold-generation
description: |
  Fix LLM-generated project scaffolds that have wrong file formats, placeholder content,
  or missing schema compliance. Use when: (1) a wizard/generator uses Claude Agent SDK to
  produce project files and the output has wrong config format, (2) generated files contain
  placeholder text like "[description]" or "<written>" instead of real code, (3) JSON
  responses are truncated due to oversized system prompts. Covers: injecting reference
  docs into system prompts, prompt size management, and placeholder detection.
author: Claude Code
version: 1.0.0
date: 2026-03-10
---

# LLM Scaffold Generation: Using Skills as Single Source of Truth

## Problem

When an LLM (via Agent SDK) generates project scaffolds, it produces wrong formats,
placeholder content, or truncated output because it doesn't know the target platform's
exact schema.

## Context / Trigger Conditions

- A wizard/generator calls Claude Agent SDK with `allowed_tools=[]` to produce JSON
  containing file contents
- Generated `config.yaml` uses wrong key names (e.g., `editable_files` instead of
  `files.editable`)
- Generated source files contain placeholder descriptions instead of real code
- JSON response is truncated mid-string ("Unterminated string" error)

## Solution

### 1. Inject Existing Skill/Reference as System Prompt

If you already have a skill (SKILL.md) or reference doc that describes the correct
format, inject it into the generator's system prompt:

```python
def _load_scaffold_reference() -> str:
    try:
        from importlib.resources import files
        return files("mypackage").joinpath("data/reference.md").read_text()
    except Exception:
        # Fallback: relative to repo root
        path = Path(__file__).resolve().parents[2] / ".claude/skills/my-skill/SKILL.md"
        return path.read_text() if path.exists() else ""
```

Bundle the file into the package at build time (hatch example):
```toml
[tool.hatch.build.targets.wheel.force-include]
".claude/skills/my-skill/SKILL.md" = "mypackage/data/reference.md"
```

### 2. Extract Only Critical Sections (Prompt Size Management)

A full skill file (400+ lines) in the system prompt leaves insufficient room for the
LLM's response, causing JSON truncation. Extract only format-critical sections:

```python
def _extract_sections(text: str, headers: list[str]) -> str:
    lines = text.split("\n")
    result, capturing = [], False
    for line in lines:
        if any(line.startswith(h) for h in headers):
            capturing = True
        elif capturing and line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            if level <= min_header_level and not any(line.startswith(h) for h in headers):
                capturing = False
                continue
        if capturing:
            result.append(line)
    return "\n".join(result)

# Only inject what the generator needs
ref = _extract_sections(full_text, [
    "### Step 2:",   # output format template
    "### Step 5:",   # config schema
    "## Common Mistakes",
])
```

### 3. Robust Placeholder Detection

LLMs find creative ways to produce placeholder content that bypasses simple checks.
Use regex to catch multiple patterns:

```python
import re
placeholder_re = re.compile(
    r"^\[.*\]$"          # [description in brackets]
    r"|^<\w+>$"          # <written>, <content>
    r"|^\.\.\.$"         # ...
    r"|^#\s*see above$"  # # see above
, re.IGNORECASE)

for path, content in files.items():
    stripped = content.strip()
    if placeholder_re.match(stripped) or len(stripped) < 20:
        raise ValueError(f"Placeholder detected in {path}")
```

### 4. System Prompt Structure

Keep the generate prompt concise with rules, not examples. Let the reference provide
the examples:

```python
GENERATE_SYSTEM_PROMPT = """
You are a scaffold generator for [Platform].
Return ONLY valid JSON with keys: "files" (dict path->content), "summary" (string).

CRITICAL RULES:
1. Every file must contain COMPLETE, REAL, FUNCTIONAL source code.
2. Follow the Reference below EXACTLY for config schema and output format.
3. [Environment-specific rules]
"""
# Then append: system += "\n\n## Reference\n\n" + trimmed_reference
```

## Verification

1. Generated config.yaml matches expected schema (correct key paths)
2. All source files have >20 chars of real code (no placeholders)
3. JSON response is complete (no truncation errors)
4. Output format matches platform's metric parsing (e.g., `grep '^metric:'`)

## Notes

- The CLAUDECODE env var must be temporarily removed when calling Agent SDK from
  within a Claude Code session (`os.environ.pop("CLAUDECODE", None)`)
- Test placeholder detection with known bad inputs in unit tests
- Monitor prompt token count: system prompt + user prompt + expected response must
  fit within the model's context window
- When the reference is updated, the generator automatically benefits (single source
  of truth)

## See Also

- `crucible-setup` skill (the reference doc used by crucible's wizard)
