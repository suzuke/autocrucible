# Prompt Optimization — Math

You are writing a system prompt that helps Claude solve math word problems accurately.

## Goal

Maximize `accuracy` on 10 hidden math word problems (0.0 to 1.0).
A score of 1.0 means Claude answered all 10 problems correctly.

## Setup

- `prompt.txt` contains the system prompt you are optimizing
- The evaluator sends your prompt to Claude with a batch of 10 math problems
- Answers are checked against exact numeric ground truth values

## Rules

- Edit only `prompt.txt`
- Prompt must be plain text (no code, no special syntax)
- Prompt length ≤ 2000 characters
- Do NOT hardcode specific answers in your prompt

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `prompt.txt`

## Hint

See `examples.txt` for 3 sample problems (different from the test set).
The test problems involve: percentages, rates, ratios, geometry basics.
Think about how to instruct Claude to show reasoning and extract numeric answers cleanly.
