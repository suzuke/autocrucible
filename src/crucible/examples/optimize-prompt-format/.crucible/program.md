# Prompt Optimization — Format Conversion

You are writing a system prompt that makes Claude convert inputs to exact output formats.

## Goal

Maximize `accuracy` on 10 hidden format conversion tasks (0.0 to 1.0).
Answers are checked by exact string match.

## Setup

- `prompt.txt` contains the system prompt you are optimizing
- The evaluator sends all 10 tasks to Claude in one batch
- Claude must output ONLY the converted result, nothing else

## Rules

- Edit only `prompt.txt`
- Prompt must be plain text
- Prompt length ≤ 2000 characters
- Do NOT hardcode specific answers in your prompt

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `prompt.txt`

## Hint

See `examples.txt` for 3 sample conversions (different from the test set).
Task types include: date formatting, unit conversion, phone normalization, number formatting.
Key: instruct Claude to output ONLY the result with NO explanation, NO surrounding text.
Exact format matters (e.g., "180.34 cm" not "approximately 180.34 cm").
