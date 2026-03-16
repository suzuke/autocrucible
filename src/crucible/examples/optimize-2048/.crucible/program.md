# 2048 Strategy Optimization

You are optimizing a 2048 game-playing AI.

## Goal

Maximize `avg_score` — the average game score over 20 games with fixed seeds.

## Interface

`choose_move(board, legal_moves, score)` is called each step:
- `board`: `list[list[int]]`, current board state (`0` = empty cell)
- `legal_moves`: `list[str]`, valid directions (`'up'`, `'down'`, `'left'`, `'right'`)
- `score`: `int`, current game score
- Returns: one of `'up'`, `'down'`, `'left'`, `'right'`

The board size is inferred from `len(board)`. Default is 4×4.

## What You Can Change (strategy.py)

- The `choose_move()` logic
- Any helper functions, classes, or data structures
- Any constants or weight matrices

## Available Context

The game engine (`game.py`) is readable. Key API for simulation:
- `Game2048(size=N, seed=S)` — create a game
- `.get_board()`, `.legal_moves()`, `.move(direction)`, `.score`, `.is_over`
- `.clone()` — deep copy for search (no history, saves memory)

## Sub-Metrics (diagnostic, not scored)

The evaluator reports these to help guide your strategy:
- `avg_monotonicity` — how ordered the board is (higher = tiles flow in one direction)
- `avg_empty_cells` — how well you manage board space (higher = less cluttered)
- `avg_move_time_ms` — your compute time per move (watch for timeout risk)
- `tile_distribution` — how often you reach each max tile

## Optimization Strategies (easy → hard)

1. **Random** → ~1000 avg_score
2. **Greedy**: pick move that maximizes immediate score → ~3000
3. **Corner strategy**: keep max tile in a corner → ~8000
4. **Weighted heuristic**: score = f(monotonicity, empty_cells, smoothness, max_corner) → ~15000
5. **Expectimax depth-2** + heuristic evaluation → ~25000
6. **Expectimax depth-3** + tuned heuristic weights → ~40000+
7. **Weight tuning**: once algorithm is solid, tune heuristic weights for more gains → ~50000+

## Hard Rules

- Only edit `strategy.py`
- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any other file
- Keep `choose_move` fast enough that 20 games complete within 300 seconds
