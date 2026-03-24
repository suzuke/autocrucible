# optimize-regex

Design a regex pattern to classify email addresses as valid or invalid.

**Requirements**: None (pure Python)

## What It Does

- Agent edits `pattern.py` to define a `PATTERN` string used with `re.fullmatch()` on each test email
- Evaluation tests against 200 labeled email addresses (valid should match, invalid should not)
- Catch-all patterns like `.*` are rejected; the regex must encode real email structure rules

## Quick Start

```bash
crucible new my-regex -e optimize-regex
cd my-regex
crucible run --tag v1
```

## Metrics

- **Metric**: f1_score (maximize) -- harmonic mean of precision and recall
- **Baseline**: ~0.5 (overly permissive pattern)
- **Eval time**: ~1-2s (30s timeout)
