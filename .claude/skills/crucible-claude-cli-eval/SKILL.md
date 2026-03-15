---
name: crucible-claude-cli-eval
description: |
  Call claude CLI as a subprocess in crucible evaluate.py to score LLM outputs.
  Use when building prompt optimization experiments (optimize-prompt-*) where the
  metric requires LLM judgment (accuracy, format compliance, reasoning quality).
  Works with Claude Code subscription — no API key needed. Covers correct flag usage
  (-p, --system-prompt), common wrong invocations, timeout sizing, and Goodhart prevention.
author: Claude Code
version: 1.0.0
date: 2026-03-13
---

# Crucible: claude CLI Subprocess Evaluation

## Problem

Prompt optimization experiments need to call Claude to score outputs, but the `anthropic` SDK requires an API key. The `claude` CLI works with a Claude Code subscription, but its flags are non-obvious and easy to get wrong.

## Correct Invocation

```python
import subprocess

result = subprocess.run(
    ["claude", "-p", "--system-prompt", system_prompt, user_message],
    capture_output=True, text=True, timeout=50
)
output = result.stdout.strip()
```

**Flag meanings:**
- `-p` / `--print`: non-interactive mode — required for subprocess calls; does NOT mean "prompt"
- `--system-prompt <text>`: separate named flag for the system prompt (not positional)

## Common Mistakes

```python
# WRONG: -p is not the system prompt argument; system_prompt becomes the user message
subprocess.run(["claude", "-p", system_prompt, user_message])

# WRONG: --system does not exist
subprocess.run(["claude", "--system", system_prompt, "-p", user_message])

# WRONG: missing -p causes interactive mode, subprocess hangs
subprocess.run(["claude", "--system-prompt", system_prompt, user_message])
```

## Timeout Sizing

Each `claude` CLI call takes 5–15s depending on response length.

| Questions per call | Subprocess timeout | `constraints.timeout_seconds` |
|--------------------|-------------------|-------------------------------|
| 1–3 | 30s | 60s |
| 5–10 (batched) | 50–80s | 120s |

Batch multiple questions into a single call with a numbered response format:
```
Q1: <number>  Q2: <number>  ...
```
This is faster than N separate subprocess calls.

If evaluation consistently times out, raise `constraints.timeout_seconds` first, then the subprocess timeout.

## Goodhart Prevention

- Embed test questions **and** answers directly inside `evaluate.py` (which is `hidden`)
- Provide only a few representative examples in a visible `examples.txt` (readonly)
- The agent sees only `examples.txt`; it cannot read the actual test set
- Scan `prompt.txt` for hardcoded answer strings if needed (AST or regex check)

## Example evaluate.py Structure

```python
# Hidden test set — agent cannot see this
TEST_CASES = [
    ("What is 15% of 80?", "12"),
    ("A train travels 60 mph for 2.5 hours. How far?", "150"),
    # ...
]

system_prompt = open("prompt.txt").read().strip()

# Batch all questions in one call
questions = "\n".join(f"Q{i+1}: {q}" for i, (q, _) in enumerate(TEST_CASES))
user_msg = questions + "\nAnswer each as: Q1: <answer> Q2: <answer> ..."

result = subprocess.run(
    ["claude", "-p", "--system-prompt", system_prompt, user_msg],
    capture_output=True, text=True, timeout=80
)

# Parse and score
correct = 0
for i, (_, expected) in enumerate(TEST_CASES):
    m = re.search(rf"Q{i+1}:\s*(\S+)", result.stdout)
    if m and m.group(1).strip() == expected:
        correct += 1

accuracy = correct / len(TEST_CASES)
print(f"accuracy: {accuracy:.4f}")
```
