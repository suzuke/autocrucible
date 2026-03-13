# Code Generator Optimization

You are writing a code generator that produces Python code for computational tasks.

## Goal

Maximize `score = mean(correctness × speed_ratio)` across 10 task specs.
- Correctness: 1.0 if `result` variable has correct value, else 0.0
- Speed ratio: min(reference_time / your_time, 10.0) — faster = higher score

## Interface

```python
def generate(spec: dict) -> str:
    """Return Python code as a string.
    The code must assign the answer to a variable named `result`.
    """
```

## Rules

- Edit only `generator.py`
- Generated code cannot import: ctypes, subprocess, os, sys, socket, urllib
- Generated code may use: math, itertools, collections, functools, heapq, bisect
- Return must be a str (Python code)

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `generator.py`

## Task Types

See `spec_schema.txt` for the spec format and 2 example tasks.
The full test set includes 10 different computational tasks.

## Strategy

For each known task type, generate the most efficient implementation:
- `sum_of_squares`: use formula n(n+1)(2n+1)//6 instead of a loop
- `find_primes`: use Sieve of Eratosthenes instead of trial division
- `fibonacci`: use iterative or formula, not recursive
- `count_vowels`: use str.count() for each vowel
- Unknown tasks: return a reasonable fallback
