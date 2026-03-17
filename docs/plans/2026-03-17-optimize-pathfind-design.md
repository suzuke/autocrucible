# optimize-pathfind Example Design

## Goal

Demonstrate `search.strategy: beam`: three independent branches explore BFS-family, A*-family, and greedy-family pathfinding algorithms simultaneously. Cross-beam history sharing prevents beams from duplicating each other's work.

## Problem

Find shortest paths in 100 random grid mazes. Metric: `nodes_explored` (total nodes visited across all mazes), minimize. Fewer nodes = smarter algorithm.

All correct algorithms find the same path length — the differentiation is efficiency (how many nodes are visited before finding the path).

## Files

| File | Role | Agent access |
|------|------|-------------|
| `pathfind.py` | Pathfinding function | editable |
| `benchmark.py` | Generates 100 mazes, counts nodes explored | hidden |

## Initial Implementation (`pathfind.py`)

```python
from collections import deque

def find_path(grid, start, end):
    """BFS pathfinding. Returns (path, nodes_explored)."""
    rows, cols = len(grid), len(grid[0])
    queue = deque([(start, [start])])
    visited = {start}
    nodes = 0

    while queue:
        (r, c), path = queue.popleft()
        nodes += 1
        if (r, c) == end:
            return path, nodes
        for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]:
            nr, nc = r+dr, c+dc
            if 0<=nr<rows and 0<=nc<cols and grid[nr][nc]==0 and (nr,nc) not in visited:
                visited.add((nr, nc))
                queue.append(((nr, nc), path + [(nr, nc)]))
    return None, nodes
```

BFS explores all directions equally — typically 40–70% of the grid nodes. A* with Manhattan distance heuristic explores ~10–20% of nodes.

## Beam Config

```yaml
search:
  strategy: beam
  beam_width: 3
  plateau_threshold: 8
```

Three beams naturally explore different families:
- **beam-0** tends to refine BFS (bidirectional, early termination)
- **beam-1** tends to find A* (heuristic guidance)
- **beam-2** tends to find greedy or jump-point search

Cross-beam history (injected into each beam's context) prevents beam-1 from implementing BFS if beam-0 already tried it.

## Benchmark (`benchmark.py`)

```python
import random
from pathfind import find_path

def generate_maze(rows, cols, seed):
    random.seed(seed)
    grid = [[0]*cols for _ in range(rows)]
    # Add random obstacles (~25%)
    for r in range(rows):
        for c in range(cols):
            if random.random() < 0.25 and (r, c) not in ((0,0), (rows-1, cols-1)):
                grid[r][c] = 1
    return grid

total_nodes = 0
mazes_solved = 0
SIZE = 20

for seed in range(100):
    grid = generate_maze(SIZE, SIZE, seed)
    path, nodes = find_path(grid, (0, 0), (SIZE-1, SIZE-1))
    if path is not None:
        total_nodes += nodes
        mazes_solved += 1

if mazes_solved < 80:
    # Penalize if too many mazes unsolved (algorithm correctness issue)
    total_nodes = 999999

print(f"nodes_explored: {total_nodes}")
```

## Config

```yaml
name: "optimize-pathfind"
files:
  editable: ["pathfind.py"]
  hidden: ["benchmark.py"]
commands:
  run: "python3 -u benchmark.py > run.log 2>&1"
  eval: "grep '^nodes_explored:' run.log"
metric:
  name: "nodes_explored"
  direction: "minimize"
constraints:
  timeout_seconds: 30
  max_retries: 3
search:
  strategy: beam
  beam_width: 3
agent:
  instructions: "program.md"
```

## Expected Beam Behavior

Each beam starts from the same BFS baseline. After 2–3 iterations:
- beam-0 refines BFS → hits bidirectional BFS ceiling (~15% improvement)
- beam-1 finds A* → major improvement (~60% fewer nodes)
- beam-2 sees beam-1 already found A*, tries greedy or jump-point

Without beam, a greedy agent would likely only find A* and stop, missing jump-point or bidirectional approaches.

## Educational Point

Beam search is serial (one agent at a time), not parallel. The advantage is breadth: beam-2 knows beam-1 already implemented A*, so it explores something genuinely different instead of reimplementing the same thing.
