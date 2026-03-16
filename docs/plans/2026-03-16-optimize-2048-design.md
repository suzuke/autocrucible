# Design: optimize-2048 Demo Project

**Date**: 2026-03-16
**Status**: Approved

## Purpose

A showcase demo for crucible, targeting non-technical audiences (investors).
Demonstrates an AI agent iteratively improving a 2048 game-playing strategy,
with visual web-based replay to make progress tangible.

## Key Requirements

- Visual: web replay showing AI getting smarter over iterations
- Scalable: board size adjustable (4×4 → 5×5 → 6×6)
- Runs 1-2 hours on M3 Max 128GB, results presented afterward
- Hybrid optimization: agent naturally transitions from algorithm changes to parameter tuning

## Project Structure

```
optimize-2048/
├── .crucible/
│   ├── config.yaml
│   └── program.md
├── game.py          # (readonly)  2048 engine, variable board size
├── strategy.py      # (editable)  agent writes strategy here
├── evaluate.py      # (hidden)    runs N games, outputs metrics + replay
├── view.html        # (readonly)  web-based replay viewer
└── .gitignore       # replay.json, run.log, __pycache__
```

## Game Engine (`game.py`)

- Standard 2048 rules: slide-merge, random spawn 2 (90%) or 4 (10%)
- `Game2048(size=4, seed=None)` — configurable board size
- API: `get_board()`, `move(direction)`, `score`, `is_over`, `max_tile`, `legal_moves()`
- `clone()` for search simulation (expectimax/MCTS)
- Move history recording for replay

## Strategy Interface (`strategy.py`)

Agent implements:

```python
def choose_move(board: list[list[int]], legal_moves: list[str], score: int) -> str:
    """Return one of: 'up', 'down', 'left', 'right'"""
```

Initial baseline: random choice. Agent is free to add helper functions, classes,
weight matrices — anything in the file.

## Evaluation (`evaluate.py`)

**Primary metric**: `avg_score` (maximize)

**Sub-metrics** (printed to stdout, visible to agent but not parsed by crucible):
- `max_tile_reached` — mode of highest tile across games
- `avg_moves` — average steps survived
- `avg_monotonicity` — board monotonicity score
- `avg_empty_cells` — average empty cells (board management quality)
- `avg_move_time_ms` — per-move compute time

**Parameters**: 50 games, fixed seeds 0-49, 120s total timeout (no per-move limit).

**Replay output**: complete board states per move saved to `replay.json`.

**Natural speed constraint**: no per-move time limit. If agent's search is too slow,
the 120s timeout triggers a crash, teaching the agent to optimize efficiency.

## Board Size Scaling

Controlled via `BOARD_SIZE` environment variable (default: 4).

Demo scenario:
1. Run crucible on 4×4 for 1-2 hours → show optimization trend
2. Take final strategy to 5×5 → score drops, demonstrating new challenge
3. Run crucible on 5×5 → show platform generality

## Web Replay Viewer (`view.html`)

- Single HTML file, zero external dependencies (inline CSS/JS)
- Load `replay.json`, select which game to watch
- Play/pause/fast-forward/rewind controls
- 2048-style colored tiles with numbers
- Display: current score, move count, max tile
- **Comparison mode**: load two replay files side-by-side (early vs late iteration)

## Config

```yaml
name: "optimize-2048"

files:
  editable:
    - "strategy.py"
  readonly:
    - "game.py"
    - "view.html"
  hidden:
    - "evaluate.py"

commands:
  run: "python3 -u evaluate.py 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "avg_score"
  direction: "maximize"

constraints:
  timeout_seconds: 120
  max_retries: 5

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

## Expected Optimization Trajectory

1. **Random** → ~1000 avg_score
2. **Greedy** (pick move that maximizes immediate score) → ~3000
3. **Corner strategy** (keep max tile in corner) → ~8000
4. **Weighted heuristic** (monotonicity + empty cells + smoothness) → ~15000
5. **Expectimax depth-2** + heuristic → ~25000
6. **Expectimax depth-3** + tuned weights → ~40000+
7. **Parameter tuning** on heuristic weights → ~50000+

The sub-metrics guide the agent toward discovering that monotonicity and
empty-cell management matter, naturally steering it from "change the algorithm"
to "tune the parameters" as algorithmic improvements plateau.
