# optimize-snake

Optimize a Snake game AI on a 10x10 board to maximize food eaten and survival time.

**Requirements**: None (pure Python)

## What It Does

- Agent edits `agent.py` to implement `choose_move(snake, food, board_size)` returning a direction
- Score formula: `avg_food_eaten x 10 + avg_steps_survived x 0.1` over 30 games
- Strategies range from greedy food-chasing to BFS pathfinding with flood-fill space management

## Quick Start

```bash
crucible new my-snake -e optimize-snake
cd my-snake
crucible run --tag v1
```

## Metrics

- **Metric**: avg_score (maximize)
- **Baseline**: ~5 (random legal moves)
- **Eval time**: ~2-10s (60s timeout)
