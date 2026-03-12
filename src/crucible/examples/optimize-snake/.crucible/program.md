# Snake Optimization

You are optimizing a 10×10 Snake game AI.

## Goal

Maximize `avg_score` — evaluated over 30 games:

```
avg_score = avg_food_eaten × 10 + avg_steps_survived × 0.1
```

## Interface

`choose_move(snake, food, board_size)` is called each step:
- `snake`: `collections.deque` of `(row, col)` tuples, `snake[0]` is the head
- `food`: `(row, col)` of current food
- `board_size`: `int` (always 10)
- Returns: `'UP'` | `'DOWN'` | `'LEFT'` | `'RIGHT'`

Illegal moves (into walls/body) are automatically redirected to a random legal move.

## What You Can Change (agent.py)

- The `choose_move()` logic
- Any hyperparameters (LOOKAHEAD_DEPTH, FOOD_WEIGHT, SPACE_WEIGHT, etc.)
- Any helper functions you add

## Optimization Strategies (easy → hard)

1. **Avoid death**: only pick from legal moves
2. **Greedy**: always move toward food (Manhattan distance)
3. **BFS**: find shortest path to food
4. **Flood fill**: prefer directions that keep the most open space
5. **Lookahead**: simulate N steps and pick the best outcome

## Hard Rules

- Only edit `agent.py`
- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any other file
