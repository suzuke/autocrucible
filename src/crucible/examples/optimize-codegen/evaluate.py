"""Evaluation harness for generator.py — DO NOT MODIFY.

Runs generator.generate(spec) for 10 task specs.
Executes generated code in a restricted namespace.
Checks correctness and measures speed vs reference naive implementation.

Output format (parsed by crucible):
    score: <float>     (mean correctness × speed_ratio across 10 tasks)
    correct_tasks: <int>
    total_tasks: <int>
"""

import time
import traceback
import sys
import math
import itertools
import collections
import functools
import heapq
import bisect

FORBIDDEN_NAMES = frozenset([
    "ctypes", "subprocess", "os", "sys", "open", "socket",
    "urllib", "__import__", "breakpoint", "input", "compile",
])

MAX_SPEED_RATIO = 10.0


def make_safe_globals():
    """Create a restricted namespace for exec."""
    safe_builtins = {}
    builtins_module = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
    for k, v in builtins_module.items():
        if k not in FORBIDDEN_NAMES:
            safe_builtins[k] = v
    return {
        "__builtins__": safe_builtins,
        "math": math,
        "itertools": itertools,
        "collections": collections,
        "functools": functools,
        "heapq": heapq,
        "bisect": bisect,
    }


def safe_exec(code: str):
    """Execute code in restricted namespace. Returns (result, error)."""
    namespace = make_safe_globals()
    try:
        exec(code, namespace)
        return namespace.get("result"), None
    except Exception as e:
        return None, str(e)


def time_fn(fn, reps=3):
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def time_code(code: str, reps=3):
    best = float("inf")
    for _ in range(reps):
        ns = make_safe_globals()
        t0 = time.perf_counter()
        exec(code, ns)
        best = min(best, time.perf_counter() - t0)
    return best


def results_equal(a, b) -> bool:
    if a is None or b is None:
        return a == b
    if type(a) != type(b):
        try:
            return sorted(str(x) for x in a) == sorted(str(x) for x in b)
        except Exception:
            return str(a) == str(b)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        # Try exact match first
        if a == b:
            return True
        # Try sorted comparison
        try:
            return sorted(str(x) for x in a) == sorted(str(x) for x in b)
        except Exception:
            return False
    if isinstance(a, dict):
        return dict(a) == dict(b)
    return a == b


# 10 test cases: (spec, expected_result, reference_code)
TEST_CASES = [
    {
        "spec": {"task": "sum_of_squares", "n": 100_000},
        "expected": sum(i*i for i in range(1, 100_001)),
        "reference_code": "result = sum(i*i for i in range(1, 100001))",
    },
    {
        "spec": {"task": "count_vowels", "text": "The quick brown fox jumps over the lazy dog " * 200},
        "expected": sum(1 for c in ("The quick brown fox jumps over the lazy dog " * 200).lower() if c in "aeiou"),
        "reference_code": "PLACEHOLDER_COUNT_VOWELS",
    },
    {
        "spec": {"task": "find_primes", "limit": 10_000},
        "expected": [i for i in range(2, 10_001) if all(i % j != 0 for j in range(2, int(i**0.5)+1))],
        "reference_code": "result = [i for i in range(2, 10001) if all(i % j != 0 for j in range(2, int(i**0.5)+1))]",
    },
    {
        "spec": {"task": "fibonacci", "n": 30},
        "expected": 832040,
        "reference_code": "f = lambda n: n if n <= 1 else f(n-1)+f(n-2); result = f(30)",
    },
    {
        "spec": {"task": "flatten", "data": [[1,[2,3]],[4,[5,[6]]],[7,8]]},
        "expected": [1, 2, 3, 4, 5, 6, 7, 8],
        "reference_code": """
def _flat(lst):
    result_list = []
    for item in lst:
        if isinstance(item, list):
            result_list.extend(_flat(item))
        else:
            result_list.append(item)
    return result_list
result = _flat([[1,[2,3]],[4,[5,[6]]],[7,8]])
""",
    },
    {
        "spec": {"task": "word_count", "text": "the cat sat on the mat " * 1000},
        "expected": {"the": 2000, "cat": 1000, "sat": 1000, "on": 1000, "mat": 1000},
        "reference_code": "PLACEHOLDER_WORD_COUNT",
    },
    {
        "spec": {"task": "matrix_trace", "n": 300},
        "expected": sum(i * 300 + i for i in range(300)),
        "reference_code": "result = sum(i * 300 + i for i in range(300))",
    },
    {
        "spec": {"task": "run_length_encode", "data": [1]*100 + [2]*50 + [3]*25 + [1]*75},
        "expected": [(1,100),(2,50),(3,25),(1,75)],
        "reference_code": """
data = [1]*100 + [2]*50 + [3]*25 + [1]*75
result = []
i = 0
while i < len(data):
    val = data[i]
    count = 0
    while i < len(data) and data[i] == val:
        count += 1
        i += 1
    result.append((val, count))
""",
    },
    {
        "spec": {"task": "gcd_list", "numbers": [48, 18, 36, 24, 72, 12, 96]},
        "expected": 6,
        "reference_code": """
import math
nums = [48, 18, 36, 24, 72, 12, 96]
result = nums[0]
for n in nums[1:]:
    result = math.gcd(result, n)
""",
    },
    {
        "spec": {"task": "anagram_groups", "words": ["eat","tea","tan","ate","nat","bat"]},
        "expected": [["ate","eat","tea"],["bat"],["nat","tan"]],
        "reference_code": """
words = ["eat","tea","tan","ate","nat","bat"]
groups = {}
for w in words:
    key = tuple(sorted(w))
    groups.setdefault(key, []).append(w)
result = sorted([sorted(g) for g in groups.values()])
""",
    },
]

# Precompute reference_code for count_vowels and word_count
TEST_CASES[1]["reference_code"] = f"text = {repr('The quick brown fox jumps over the lazy dog ' * 200)}; result = sum(1 for c in text.lower() if c in 'aeiou')"
TEST_CASES[5]["reference_code"] = f"import collections; text = {repr('the cat sat on the mat ' * 1000)}; result = dict(collections.Counter(text.split()))"


def main():
    try:
        from generator import generate

        total_score = 0.0
        correct_tasks = 0

        for i, tc in enumerate(TEST_CASES):
            spec = tc["spec"]
            expected = tc["expected"]
            ref_code = tc["reference_code"]
            task_name = spec["task"]

            # Generate code
            try:
                code = generate(spec)
            except Exception as e:
                print(f"Task {i+1} ({task_name}): generate() error: {e}")
                continue

            if not isinstance(code, str):
                print(f"Task {i+1} ({task_name}): generate() must return str, got {type(code)}")
                continue

            # Execute and check correctness
            result, error = safe_exec(code)
            if error:
                print(f"Task {i+1} ({task_name}): exec error: {error[:100]}")
                continue

            correct = results_equal(result, expected)
            if not correct:
                print(f"Task {i+1} ({task_name}): wrong answer (expected {type(expected).__name__})")
                continue

            correct_tasks += 1

            # Time generated code vs reference
            try:
                generated_time = time_code(code, reps=3)
                ref_time = time_code(ref_code, reps=3)
                speed_ratio = min(ref_time / generated_time, MAX_SPEED_RATIO) if generated_time > 0 else 1.0
            except Exception:
                speed_ratio = 1.0

            task_score = 1.0 * speed_ratio
            total_score += task_score
            print(f"Task {i+1} ({task_name}): correct ✓ speed_ratio={speed_ratio:.2f} score={task_score:.2f}")

        mean_score = total_score / len(TEST_CASES)
        print(f"score: {mean_score:.4f}")
        print(f"correct_tasks: {correct_tasks}")
        print(f"total_tasks: {len(TEST_CASES)}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("score: 0.0")


if __name__ == "__main__":
    main()
