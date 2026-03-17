# Pathfinding Optimization

You are optimizing a pathfinding algorithm to minimize nodes explored in grid mazes.

## Goal

Minimize `nodes_explored` — total nodes visited across 100 test mazes. Fewer nodes = smarter algorithm.

## Rules

- Edit only `pathfind.py`
- Your `find_path(grid, start, end)` function must return `(path, nodes_explored)`
  - `path`: list of (row, col) tuples from start to end, or `None` if no path exists
  - `nodes_explored`: integer count of nodes visited during search
- The path must be valid (each step moves to an adjacent, non-obstacle cell)
- The function signature must remain `def find_path(grid, start, end):`
- Standard library only

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `pathfind.py`
