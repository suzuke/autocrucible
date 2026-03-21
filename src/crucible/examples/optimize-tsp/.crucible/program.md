# Optimize TSP Solver

## Goal

Minimize `total_distance` — the total Euclidean distance of a round-trip route visiting all 200 cities exactly once and returning to the start.

## Setup

- `cities.py` generates 200 cities with fixed coordinates (seeded RNG, deterministic).
- Each city has an `(x, y)` position in a 1000×1000 grid.
- The evaluation runs your solver 3 times and takes the median distance.

## Interface

You must implement `solve(cities: list[tuple[float, float]]) -> list[int]` in `solver.py`.

- **Input:** list of 200 `(x, y)` coordinate tuples.
- **Output:** list of 200 integers — a permutation of `[0, 1, ..., 199]` representing the visit order.
- The route is: `order[0] → order[1] → ... → order[199] → order[0]` (returns to start).

## Rules

- You may only modify `solver.py`.
- The function signature `solve(cities)` must be preserved.
- Must return a valid permutation (all 200 city indices, each exactly once).
- Standard library only — no external packages (numpy, scipy, etc.).
- Must complete within 10 seconds per call.

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically.
- DO NOT modify any file other than `solver.py`.

## Optimization Strategies

Ordered from easy to hard:

1. **Nearest neighbor heuristic** — greedy, pick the closest unvisited city each step.
2. **2-opt local search** — iteratively reverse sub-routes to remove crossings.
3. **Or-opt / 3-opt** — move segments or try 3-edge swaps for deeper improvements.
4. **Simulated annealing** — probabilistic acceptance of worse moves to escape local optima.
5. **Lin-Kernighan style** — variable-depth search with backtracking.
6. **Hybrid** — combine construction heuristic + local search + metaheuristic.
