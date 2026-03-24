# optimize-prompt-format

Optimize a system prompt that makes Claude convert inputs to exact output formats (dates, units, phone numbers).

**Requirements**: Anthropic API key (uses Claude for evaluation)

## What It Does

- Agent edits `prompt.txt` to craft a system prompt (max 2,000 chars) for format conversion tasks
- Evaluation sends 10 hidden format conversion tasks to Claude with the prompt and checks exact string match
- Task types include date formatting, unit conversion, phone normalization, and number formatting

## Quick Start

```bash
crucible new my-prompt-format -e optimize-prompt-format
cd my-prompt-format
crucible run --tag v1
```

## Metrics

- **Metric**: accuracy (maximize) -- 0.0 to 1.0
- **Baseline**: ~0.3 (minimal prompt)
- **Eval time**: ~10-30s (120s timeout, depends on API latency)
