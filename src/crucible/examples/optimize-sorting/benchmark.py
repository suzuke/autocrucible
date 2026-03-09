"""Benchmark harness for sort.py — DO NOT MODIFY.

Measures how many sort operations can be completed per second on
randomly generated arrays of 100,000 integers. Verifies correctness
after each sort.

Output format (parsed by crucible):
    ops_per_sec: <float>
    avg_ms:      <float>
    correct:     <bool>
"""

import random
import time

ARRAY_SIZE = 100_000
NUM_TRIALS = 10
SEED = 42


def generate_array(rng: random.Random) -> list[int]:
    return [rng.randint(-(10**9), 10**9) for _ in range(ARRAY_SIZE)]


def verify_sorted(arr: list[int]) -> bool:
    for i in range(len(arr) - 1):
        if arr[i] > arr[i + 1]:
            return False
    return True


def main():
    rng = random.Random(SEED)

    # Import the function under test
    from sort import sort_array

    # Warm up
    warm = generate_array(rng)
    sort_array(warm)

    # Benchmark
    times = []
    correct = True
    for _ in range(NUM_TRIALS):
        arr = generate_array(rng)
        expected = sorted(arr)

        t0 = time.perf_counter()
        result = sort_array(arr)
        t1 = time.perf_counter()

        times.append(t1 - t0)

        if result != expected:
            correct = False
            break

    if not correct:
        print("ops_per_sec: 0.0")
        print("avg_ms: 0.0")
        print("correct: false")
        return

    avg_sec = sum(times) / len(times)
    ops = 1.0 / avg_sec if avg_sec > 0 else 0.0
    avg_ms = avg_sec * 1000

    print(f"ops_per_sec: {ops:.2f}")
    print(f"avg_ms: {avg_ms:.2f}")
    print(f"correct: true")


if __name__ == "__main__":
    main()
