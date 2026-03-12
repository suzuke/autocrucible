# optimize-snake Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create `src/crucible/examples/optimize-snake/` — a self-contained crucible example where an agent optimizes a Snake game AI using pure heuristic search.

**Architecture:** Pure Python, no external dependencies. `game.py` (readonly engine) + `agent.py` (editable baseline) + `evaluate.py` (hidden harness). Metric = avg_score = food×10 + steps×0.1 over 30 fixed-seed games.

**Tech Stack:** Python stdlib only (`collections.deque`, `random`)

---

### Task 1: Create game.py (Snake engine)

**Files:**
- Create: `src/crucible/examples/optimize-snake/game.py`
- Test: `tests/test_snake_game.py`

**Step 1: Write failing tests**

```python
# tests/test_snake_game.py
import sys
sys.path.insert(0, "src/crucible/examples/optimize-snake")
from game import SnakeGame

def test_initial_state():
    g = SnakeGame(seed=0)
    assert g.board_size == 10
    assert len(g.snake) >= 1
    assert g.food is not None
    assert not g.done
    assert g.steps == 0
    assert g.food_eaten == 0

def test_legal_moves_avoids_walls():
    """Snake at top-left corner should not return UP or LEFT."""
    g = SnakeGame(seed=0)
    # Force snake head to corner
    g.snake = __import__('collections').deque([(0, 0)])
    g.occupied = {(0, 0)}
    moves = g.legal_moves()
    assert 'UP' not in moves
    assert 'LEFT' not in moves

def test_step_moves_snake():
    g = SnakeGame(seed=0)
    head_before = g.snake[0]
    g.step('DOWN')
    head_after = g.snake[0]
    assert head_after == (head_before[0] + 1, head_before[1])

def test_step_eats_food():
    """Place food directly below head, step DOWN, food_eaten should increase."""
    g = SnakeGame(seed=0)
    r, c = g.snake[0]
    g.food = (r + 1, c)
    g.step('DOWN')
    assert g.food_eaten == 1

def test_step_wall_collision_ends_game():
    g = SnakeGame(seed=0)
    g.snake = __import__('collections').deque([(0, 0)])
    g.occupied = {(0, 0)}
    g.step('UP')  # hits wall
    assert g.done

def test_step_self_collision_ends_game():
    """Build a snake body and walk into it."""
    import collections
    g = SnakeGame(seed=0)
    # Snake: head at (5,5), body going right: (5,6), (5,7)
    g.snake = collections.deque([(5, 5), (5, 6), (5, 7)])
    g.occupied = {(5, 5), (5, 6), (5, 7)}
    g.food = (0, 0)
    g.step('RIGHT')  # head moves to (5,6) — body collision
    assert g.done

def test_food_respawns_after_eating():
    g = SnakeGame(seed=0)
    r, c = g.snake[0]
    old_food = g.food
    g.food = (r + 1, c)
    g.step('DOWN')
    assert g.food != old_food or g.done

def test_step_limit():
    """Game should not exceed MAX_STEPS."""
    g = SnakeGame(seed=0)
    for _ in range(1100):
        if g.done:
            break
        moves = g.legal_moves()
        if moves:
            g.step(moves[0])
        else:
            break
    assert g.steps <= 1000 or g.done
```

**Step 2: Run to verify they fail**

```bash
cd /Users/suzuke/Documents/Hack/crucible
uv run pytest tests/test_snake_game.py -v 2>&1 | head -30
```
Expected: ImportError or ModuleNotFoundError (game.py doesn't exist yet)

**Step 3: Write game.py**

```python
"""Snake game engine — DO NOT MODIFY.

10×10 board. Snake eats food, grows, and must avoid walls and itself.
Game ends on collision or after MAX_STEPS steps.

Interface:
    SnakeGame(seed=None)
        .snake       — deque of (row, col), snake[0] is head
        .food        — (row, col)
        .board_size  — int (10)
        .done        — bool
        .steps       — int
        .food_eaten  — int
        .legal_moves()  — list of valid directions
        .step(direction) — advance game; direction in UP/DOWN/LEFT/RIGHT
"""

import collections
import random

BOARD_SIZE = 10
MAX_STEPS = 1000

DELTAS = {
    'UP':    (-1,  0),
    'DOWN':  ( 1,  0),
    'LEFT':  ( 0, -1),
    'RIGHT': ( 0,  1),
}


class SnakeGame:
    def __init__(self, seed=None):
        self.board_size = BOARD_SIZE
        self.rng = random.Random(seed)
        self.done = False
        self.steps = 0
        self.food_eaten = 0

        # Place snake at random position in the middle area
        start_r = self.rng.randint(2, BOARD_SIZE - 3)
        start_c = self.rng.randint(2, BOARD_SIZE - 3)
        self.snake = collections.deque([(start_r, start_c)])
        self.occupied = {(start_r, start_c)}

        self.food = self._place_food()

    def _place_food(self):
        """Place food on a random empty cell."""
        empty = [
            (r, c)
            for r in range(self.board_size)
            for c in range(self.board_size)
            if (r, c) not in self.occupied
        ]
        if not empty:
            return None
        return self.rng.choice(empty)

    def legal_moves(self):
        """Return list of directions that don't immediately hit a wall or body."""
        if self.done:
            return []
        head_r, head_c = self.snake[0]
        valid = []
        for direction, (dr, dc) in DELTAS.items():
            nr, nc = head_r + dr, head_c + dc
            if 0 <= nr < self.board_size and 0 <= nc < self.board_size:
                if (nr, nc) not in self.occupied or (nr, nc) == self.snake[-1]:
                    valid.append(direction)
        return valid

    def step(self, direction):
        """Advance game by one step. Invalid direction ends game immediately."""
        if self.done:
            return

        self.steps += 1

        if direction not in DELTAS:
            self.done = True
            return

        head_r, head_c = self.snake[0]
        dr, dc = DELTAS[direction]
        new_r, new_c = head_r + dr, head_c + dc

        # Wall collision
        if not (0 <= new_r < self.board_size and 0 <= new_c < self.board_size):
            self.done = True
            return

        # Move: remove tail first (so tail cell is free if snake moves into it)
        tail = self.snake[-1]
        self.occupied.discard(tail)

        # Self-collision check (tail already removed)
        if (new_r, new_c) in self.occupied:
            self.done = True
            return

        # Apply move
        self.snake.appendleft((new_r, new_c))
        self.occupied.add((new_r, new_c))

        if (new_r, new_c) == self.food:
            # Eat: grow (add tail back)
            self.snake.append(tail)
            self.occupied.add(tail)
            self.food_eaten += 1
            self.food = self._place_food()
            if self.food is None:
                self.done = True  # board full
        else:
            # No eat: tail already removed
            pass

        if self.steps >= MAX_STEPS:
            self.done = True
```

**Step 4: Run tests**

```bash
uv run pytest tests/test_snake_game.py -v
```
Expected: all 8 tests PASS

**Step 5: Commit**

```bash
git add src/crucible/examples/optimize-snake/game.py tests/test_snake_game.py
git commit -m "feat: add optimize-snake game engine with tests"
```

---

### Task 2: Create agent.py (random baseline)

**Files:**
- Create: `src/crucible/examples/optimize-snake/agent.py`

**Step 1: Write the file**

No test needed — evaluate.py will exercise it. Just verify it's importable and returns a valid direction.

```python
"""Snake AI agent — this is the file the agent optimizes.

Baseline: random move from legal moves.

The agent optimizes choose_move() and any hyperparameters below.
"""

import random

# --- Tunable hyperparameters ---
LOOKAHEAD_DEPTH = 0   # how many steps to look ahead (0 = disabled)
FOOD_WEIGHT = 1.0     # weight for food-seeking behaviour
SPACE_WEIGHT = 0.0    # weight for open-space preference


def choose_move(snake, food, board_size):
    """Choose next direction for the snake.

    Args:
        snake:      collections.deque of (row, col), snake[0] is head
        food:       (row, col) of current food
        board_size: int (always 10)

    Returns:
        direction: one of 'UP', 'DOWN', 'LEFT', 'RIGHT'
    """
    return random.choice(['UP', 'DOWN', 'LEFT', 'RIGHT'])
```

**Step 2: Smoke-test import**

```bash
cd src/crucible/examples/optimize-snake && python3 -c "from agent import choose_move; import collections; print(choose_move(collections.deque([(5,5)]), (3,3), 10))"
```
Expected: one of UP / DOWN / LEFT / RIGHT

**Step 3: Commit**

```bash
git add src/crucible/examples/optimize-snake/agent.py
git commit -m "feat: add optimize-snake random baseline agent"
```

---

### Task 3: Create evaluate.py (hidden harness)

**Files:**
- Create: `src/crucible/examples/optimize-snake/evaluate.py`

**Step 1: Write evaluate.py**

```python
"""Evaluation harness for Snake agent — DO NOT MODIFY.

Runs 30 games with fixed seeds. Measures avg_score, avg_food_eaten,
avg_steps_survived.

Output format (parsed by crucible):
    avg_score:        <float>   (primary metric = food*10 + steps*0.1)
    avg_food_eaten:   <float>
    avg_steps:        <float>
    games_played:     <int>
"""

import traceback

from game import SnakeGame

NUM_GAMES = 30
SEED_OFFSET = 0  # game seed = SEED_OFFSET + game_index


def run_game(game_seed):
    """Run one game. Returns (food_eaten, steps_survived)."""
    game = SnakeGame(seed=game_seed)

    try:
        from agent import choose_move
    except Exception as e:
        print(f"ERROR importing agent: {e}")
        return 0, 0

    while not game.done:
        try:
            direction = choose_move(
                game.snake,
                game.food,
                game.board_size,
            )
        except Exception:
            # Agent crashed — treat as random
            import random
            moves = game.legal_moves()
            direction = random.choice(moves) if moves else 'UP'

        # If direction is illegal, pick a random legal move instead
        legal = game.legal_moves()
        if direction not in legal:
            import random
            direction = random.choice(legal) if legal else direction

        game.step(direction)

    return game.food_eaten, game.steps


def main():
    total_food = 0.0
    total_steps = 0.0
    games_played = 0

    for i in range(NUM_GAMES):
        food, steps = run_game(SEED_OFFSET + i)
        total_food += food
        total_steps += steps
        games_played += 1

    avg_food = total_food / games_played
    avg_steps = total_steps / games_played
    avg_score = avg_food * 10 + avg_steps * 0.1

    print(f"games_played: {games_played}")
    print(f"avg_food_eaten: {avg_food:.2f}")
    print(f"avg_steps: {avg_steps:.2f}")
    print(f"avg_score: {avg_score:.2f}")


if __name__ == "__main__":
    main()
```

**Step 2: Run to verify output format**

```bash
cd src/crucible/examples/optimize-snake && python3 -u evaluate.py
```
Expected output (random baseline, approximate):
```
games_played: 30
avg_food_eaten: 0.xx
avg_steps: xx.xx
avg_score: x.xx
```
`avg_score` should be in roughly 3–8 range for random baseline.

**Step 3: Commit**

```bash
git add src/crucible/examples/optimize-snake/evaluate.py
git commit -m "feat: add optimize-snake evaluation harness"
```

---

### Task 4: Create config.yaml and program.md

**Files:**
- Create: `src/crucible/examples/optimize-snake/.crucible/config.yaml`
- Create: `src/crucible/examples/optimize-snake/.crucible/program.md`

**Step 1: Write config.yaml**

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

**Step 2: Write program.md**

```markdown
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

1. **Avoid death**: only pick directions from `legal_moves()` equivalent logic
2. **Greedy**: always move toward food (Manhattan distance)
3. **BFS**: find shortest path to food
4. **Flood fill**: prefer directions that keep the most open space
5. **Lookahead**: simulate N steps and pick the best outcome

## Hard Rules

- Only edit `agent.py`
- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any other file
```

**Step 3: Commit**

```bash
git add src/crucible/examples/optimize-snake/.crucible/
git commit -m "feat: add optimize-snake crucible config and program.md"
```

---

### Task 5: Add .gitignore and validate

**Files:**
- Create: `src/crucible/examples/optimize-snake/.gitignore`

**Step 1: Write .gitignore**

```
run.log
__pycache__/
*.pyc
```

**Step 2: Run crucible validate**

```bash
cd src/crucible/examples/optimize-snake && uv run crucible validate
```
Expected: all checks pass (config valid, files present, metric parseable)

**Step 3: Run one full crucible iteration manually**

```bash
cd src/crucible/examples/optimize-snake && python3 -u evaluate.py
```
Confirm `avg_score:` appears in output and is a float.

**Step 4: Commit**

```bash
git add src/crucible/examples/optimize-snake/.gitignore
git commit -m "chore: add optimize-snake .gitignore"
```

---

### Task 6: Add to crucible examples registry (if exists)

**Step 1: Check if there is a registry**

```bash
grep -r "optimize-sorting\|optimize-compress" src/crucible/ --include="*.py" --include="*.md" -l
```

If any file lists examples, add `optimize-snake` to it.

**Step 2: Commit if changed**

```bash
git add -u
git commit -m "docs: register optimize-snake in examples list"
```

---

## Verification

After all tasks, the example should pass this end-to-end check:

```bash
cd src/crucible/examples/optimize-snake
python3 -u evaluate.py
# Expect: avg_score line with a small positive number (~3-8)
```

And a full crucible run should work:
```bash
cd src/crucible/examples/optimize-snake
uv run crucible run --tag test-snake --iterations 2
```
