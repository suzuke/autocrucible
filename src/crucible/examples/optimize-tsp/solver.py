"""
TSP Solver — optimize this file to minimize total route distance.

Implement solve(cities) -> list[int] that returns a visit order
for all cities, minimizing the total Euclidean round-trip distance.
"""

import math


def solve(cities: list[tuple[float, float]]) -> list[int]:
    """
    Find a short route visiting all cities exactly once and returning to start.

    Args:
        cities: list of (x, y) coordinates for each city.

    Returns:
        A permutation of [0, 1, ..., len(cities)-1] representing visit order.
    """
    # Baseline: visit cities in the order they appear (index order).
    # This is the worst reasonable approach — should be easy to beat.
    return list(range(len(cities)))
