"""
TSP evaluation harness — measures total round-trip distance.
Hidden from the agent.
"""

import math
import time
import sys

from cities import generate_cities

TIMEOUT_SECONDS = 10


def euclidean(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def route_distance(cities: list[tuple[float, float]], order: list[int]) -> float:
    """Calculate total round-trip distance for a given visit order."""
    total = 0.0
    n = len(order)
    for i in range(n):
        total += euclidean(cities[order[i]], cities[order[(i + 1) % n]])
    return total


def validate_solution(order: list[int], num_cities: int) -> str | None:
    """Return error message if solution is invalid, None if valid."""
    if not isinstance(order, list):
        return f"Expected list, got {type(order).__name__}"
    if len(order) != num_cities:
        return f"Expected {num_cities} cities, got {len(order)}"
    if sorted(order) != list(range(num_cities)):
        missing = set(range(num_cities)) - set(order)
        duplicated = [x for x in order if order.count(x) > 1]
        return f"Invalid permutation. Missing: {missing}, Duplicated: {set(duplicated)}"
    return None


def main():
    cities = generate_cities()
    num_cities = len(cities)

    # Import solver
    try:
        from solver import solve
    except Exception as e:
        print(f"ERROR: Failed to import solver: {e}")
        print(f"total_distance: 999999.0")
        print(f"valid: false")
        return

    # Run solver with timeout check
    t0 = time.perf_counter()
    try:
        order = solve(cities)
    except Exception as e:
        print(f"ERROR: Solver raised exception: {e}")
        print(f"total_distance: 999999.0")
        print(f"valid: false")
        return
    elapsed = time.perf_counter() - t0

    if elapsed > TIMEOUT_SECONDS:
        print(f"ERROR: Solver took {elapsed:.1f}s (limit: {TIMEOUT_SECONDS}s)")
        print(f"total_distance: 999999.0")
        print(f"valid: false")
        print(f"elapsed_seconds: {elapsed:.3f}")
        return

    # Validate
    error = validate_solution(order, num_cities)
    if error:
        print(f"ERROR: {error}")
        print(f"total_distance: 999999.0")
        print(f"valid: false")
        print(f"elapsed_seconds: {elapsed:.3f}")
        return

    # Calculate distance
    dist = route_distance(cities, order)

    # Also compute naive baseline for reference
    naive_order = list(range(num_cities))
    naive_dist = route_distance(cities, naive_order)
    improvement = (1 - dist / naive_dist) * 100

    print(f"total_distance: {dist:.2f}")
    print(f"valid: true")
    print(f"elapsed_seconds: {elapsed:.3f}")
    print(f"naive_baseline: {naive_dist:.2f}")
    print(f"improvement_pct: {improvement:.1f}")


if __name__ == "__main__":
    main()
