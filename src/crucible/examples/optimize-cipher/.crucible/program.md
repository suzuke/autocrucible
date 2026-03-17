# Cipher Throughput Optimization

You are optimizing a substitution cipher to maximize encryption throughput.

## Goal

Maximize `throughput` — characters encrypted per second on 1 MB of text.

## Rules

- Edit only `cipher.py`
- Your `encrypt(text: str, key: dict) -> str` function must correctly apply the key mapping
- Characters not in the key must pass through unchanged
- The function signature must remain `def encrypt(text: str, key: dict) -> str:`
- Standard library only

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `cipher.py`
