# Regex Optimization

You are designing a regex pattern to classify email addresses.

## Goal

Maximize `f1_score` on a held-out set of 200 labeled email addresses.
F1 = harmonic mean of precision and recall.

## Setup

- `pattern.py` contains `PATTERN: str` — a single Python regex string
- The evaluator runs `re.fullmatch(PATTERN, email)` on each test address
- VALID emails → should match; INVALID emails → should not match

## Rules

- Edit only `pattern.py`
- `PATTERN` must be a valid Python regex string
- Pattern must process all test samples in under 2 seconds total
- Catch-all patterns like `.*` or `\S+` alone are rejected
- Standard library `re` module only

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `pattern.py`

## Reference

See `examples.txt` for 20 valid and 20 invalid examples (different from the test set).

Common email structure: `local-part @ domain . tld`
- Local part: letters, digits, `.`, `+`, `-`, `_` (no spaces, not starting/ending with `.`)
- Domain: labels separated by `.`, each label: letters/digits/hyphens (no leading/trailing hyphen)
- TLD: 2+ letters only
