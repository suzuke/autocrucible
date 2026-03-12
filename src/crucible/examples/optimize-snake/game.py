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
DELTAS = {'UP': (-1, 0), 'DOWN': (1, 0), 'LEFT': (0, -1), 'RIGHT': (0, 1)}


class SnakeGame:
    def __init__(self, seed=None):
        self._rng = random.Random(seed)
        self.board_size = BOARD_SIZE
        self.done = False
        self.steps = 0
        self.food_eaten = 0

        # Start snake as single cell in middle area (rows 2-7, cols 2-7)
        start_r = self._rng.randint(2, 7)
        start_c = self._rng.randint(2, 7)
        self.snake = collections.deque([(start_r, start_c)])
        self.occupied = {(start_r, start_c)}

        self.food = self._place_food()

    def _place_food(self):
        """Place food on a random empty cell; returns None if board is full."""
        empty = [
            (r, c)
            for r in range(self.board_size)
            for c in range(self.board_size)
            if (r, c) not in self.occupied
        ]
        if not empty:
            return None
        return self._rng.choice(empty)

    def legal_moves(self):
        """Return directions that don't immediately hit a wall or body.

        The tail cell is considered free because it will move away before the
        head arrives there (unless the snake just ate food and grew).
        """
        head_r, head_c = self.snake[0]
        # The tail will vacate its cell on the next step (unless eating, but
        # for legality we optimistically treat it as free).
        tail = self.snake[-1]
        moves = []
        for direction, (dr, dc) in DELTAS.items():
            nr, nc = head_r + dr, head_c + dc
            # Wall check
            if not (0 <= nr < self.board_size and 0 <= nc < self.board_size):
                continue
            # Body check — tail is free (it will move away)
            if (nr, nc) in self.occupied and (nr, nc) != tail:
                continue
            moves.append(direction)
        return moves

    def step(self, direction):
        """Advance the game by one step in the given direction."""
        if self.done:
            return

        self.steps += 1

        # Validate direction
        if direction not in DELTAS:
            self.done = True
            return

        head_r, head_c = self.snake[0]
        dr, dc = DELTAS[direction]
        nr, nc = head_r + dr, head_c + dc

        # Wall collision
        if not (0 <= nr < self.board_size and 0 <= nc < self.board_size):
            self.done = True
            return

        # Remove tail FIRST (so snake can move into where tail was)
        tail = self.snake.pop()
        self.occupied.discard(tail)

        # Self-collision check (after tail removal)
        if (nr, nc) in self.occupied:
            self.done = True
            return

        # Move head
        self.snake.appendleft((nr, nc))
        self.occupied.add((nr, nc))

        # Eat food
        if (nr, nc) == self.food:
            self.food_eaten += 1
            # Grow: put tail back
            self.snake.append(tail)
            self.occupied.add(tail)
            self.food = self._place_food()

        # Step limit
        if self.steps >= MAX_STEPS:
            self.done = True
