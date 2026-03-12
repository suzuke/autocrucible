# optimize-snake Design

Date: 2026-03-12

## Summary

A new crucible example that optimizes a Snake game AI using pure heuristic search.
No external dependencies. Fits within a 60s timeout.

## Decisions

| Parameter | Choice | Reason |
|-----------|--------|--------|
| Board size | 10×10 | Fast per-game execution, wide enough search space |
| Editable scope | AI logic + hyperparameters | Agent can explore algorithm improvements and tuning |
| Metric | avg_score = food×10 + steps×0.1 | Rewards both eating and survival, prevents loop-farming |
| Baseline | Random movement | Maximum optimization headroom |
| Timeout | 60s | Consistent with other pure-algorithm examples |

## File Structure

```
src/crucible/examples/optimize-snake/
├── .crucible/
│   ├── config.yaml
│   └── program.md
├── game.py          # readonly — game engine (SnakeGame)
├── agent.py         # editable — AI decision logic + hyperparameters
└── evaluate.py      # hidden — evaluation harness
```

## Architecture

### game.py (readonly)

- `SnakeGame`: 10×10 board, snake as `collections.deque` of `(row, col)`, single food item
- `step(direction)` → returns `(reward, done, info)`
- `legal_moves()` → list of valid directions from current head
- Directions: `'UP' | 'DOWN' | 'LEFT' | 'RIGHT'`
- Game ends on wall collision or self-collision

### agent.py (editable)

Baseline: random choice from legal moves.

```python
# Tunable hyperparameters
LOOKAHEAD_DEPTH = 0
FOOD_WEIGHT = 1.0
SPACE_WEIGHT = 0.0

def choose_move(snake, food, board_size):
    """Return direction: 'UP'|'DOWN'|'LEFT'|'RIGHT'"""
    return random.choice(['UP', 'DOWN', 'LEFT', 'RIGHT'])
```

Agent may add helper functions (BFS, flood fill, lookahead, etc.).

### evaluate.py (hidden)

- 30 games with fixed seeds 0–29
- Per-game step limit: 1000 (prevents infinite loop-farming)
- `avg_score = mean(food_eaten * 10 + steps_survived * 0.1)` across all games
- Invalid direction from agent → fall back to random legal move (avoid instant death on edge cases)
- Exception in `choose_move` → game scores 0

## Metric

```
avg_score = avg_food_eaten × 10 + avg_steps_survived × 0.1
```

Expected ranges:

| Strategy | Typical avg_score |
|----------|------------------|
| Random | 3–8 |
| Greedy (toward food) | 20–40 |
| BFS shortest path | 50–100 |
| BFS + flood fill | 80–200 |
| Near-optimal | 500+ |

## Goodhart's Law Protection

1. `evaluate.py` is **hidden** — agent cannot read scoring logic
2. Fixed seeds — reproducible, luck-independent
3. Step cap 1000 — survival bonus is bounded, food score dominates
4. `game.py` is **readonly** — game rules cannot be modified

## config.yaml

```yaml
name: "optimize-snake"

files:
  editable:
    - "agent.py"
  readonly:
    - "game.py"
  hidden:
    - "evaluate.py"

commands:
  run: "python3 -u evaluate.py 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "avg_score"
  direction: "maximize"

constraints:
  timeout_seconds: 60
  max_retries: 3

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

## program.md (agent instructions)

Goal: maximize `avg_score`. Agent can modify `choose_move` logic and any hyperparameters.
Suggested optimization path: random → avoid-death → greedy → BFS → flood fill → lookahead.
Hard rules: only edit `agent.py`, do not execute scripts.
