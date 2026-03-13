# Prompt Optimization — Logic

You are writing a system prompt that helps Claude solve logic reasoning problems.

## Goal

Maximize `accuracy` on 10 hidden logical reasoning problems (0.0 to 1.0).

## Setup

- `prompt.txt` contains the system prompt you are optimizing
- The evaluator sends your prompt to Claude with 10 logic problems
- Answers are: True, False, or Cannot determine

## Rules

- Edit only `prompt.txt`
- Prompt must be plain text
- Prompt length ≤ 2000 characters
- Do NOT hardcode specific answers in your prompt

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `prompt.txt`

## Hint

See `examples.txt` for 3 sample problems (different from the test set).
The test problems involve: syllogisms, modus ponens, affirming the consequent (fallacy), set membership.
Key challenge: distinguish valid inferences from invalid ones (e.g., "All A are B, x is B" does NOT mean "x is A").
