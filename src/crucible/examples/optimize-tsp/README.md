# optimize-tsp

Minimize the total route distance for a Travelling Salesman Problem with 200 cities.

**Requirements**: None (pure Python, no external packages)

## What It Does

- Agent edits `solver.py` to implement `solve(cities) -> list[int]` returning a visit order permutation
- Cities are 200 fixed points in a 1000x1000 grid; evaluation runs the solver 3 times and takes the median
- Strategies range from nearest-neighbor heuristic to 2-opt/3-opt local search and simulated annealing

## Quick Start

```bash
crucible new my-tsp -e optimize-tsp
cd my-tsp
crucible run --tag v1
```

## Metrics

- **Metric**: total_distance (minimize) -- Euclidean round-trip distance
- **Baseline**: ~15,000-20,000 (nearest neighbor heuristic)
- **Eval time**: ~5-30s (10s per call, 3 runs, 120s timeout)
