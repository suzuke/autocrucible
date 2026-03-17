# optimize-pathfind

Demonstrates **beam search strategy** (v0.5.0): three independent branches explore different pathfinding algorithm families simultaneously, with cross-beam history sharing preventing redundant exploration.

## The Problem

Find shortest paths in 100 random 20×20 grid mazes. BFS explores every direction equally and visits ~40–70% of grid cells. Smarter algorithms (A*, bidirectional BFS) visit far fewer nodes.

## Demo Walkthrough

```bash
crucible run --tag pathfind-v1
```

Expected pattern with `beam_width: 3`:
- **beam-0**: refines BFS → bidirectional BFS (~15–20% improvement)
- **beam-1**: discovers A* with Manhattan heuristic (~50–60% fewer nodes)
- **beam-2**: sees beam-1 already found A* (via cross-beam history), explores jump-point search or greedy best-first instead

Without beam, a greedy agent would likely converge on A* and stop. Beam ensures the third approach (jump-point, IDA*) also gets explored.

## Why Beam Matters

Beam search is **serial** — one agent at a time, not parallel. The cost is proportional to iterations, not multiplied by beam_width. The advantage is **exploration breadth**: each beam contributes different knowledge to the shared history.

## Config

```yaml
search:
  strategy: beam
  beam_width: 3
```
