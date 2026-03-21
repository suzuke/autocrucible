"""
Deterministic city generator for TSP benchmark.
200 cities on a 1000x1000 grid with fixed seed.
"""

import random

NUM_CITIES = 200
GRID_SIZE = 1000.0
SEED = 42


def generate_cities() -> list[tuple[float, float]]:
    """Generate deterministic city coordinates."""
    rng = random.Random(SEED)
    return [(rng.uniform(0, GRID_SIZE), rng.uniform(0, GRID_SIZE)) for _ in range(NUM_CITIES)]


if __name__ == "__main__":
    cities = generate_cities()
    print(f"Generated {len(cities)} cities")
    for i, (x, y) in enumerate(cities[:5]):
        print(f"  City {i}: ({x:.1f}, {y:.1f})")
    print("  ...")
