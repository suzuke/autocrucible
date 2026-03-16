"""2048 game engine — DO NOT MODIFY.

Standard 2048 rules with variable board size. Tiles slide and merge;
equal adjacent tiles merge once per move. New tile (2 at 90%, 4 at 10%)
spawns on a random empty cell after each valid move.

Interface:
    Game2048(size=None, seed=None)   # defaults to BOARD_SIZE env var or 4
        .board          — list[list[int]], 0 = empty
        .score          — int, total score from merges
        .is_over        — bool, True when no legal moves remain
        .max_tile       — int, highest tile on the board
        .move_count     — int, number of successful moves made
        .history        — list of (board_snapshot, score, move_direction) tuples
        .get_board()    — returns a deep copy of the board
        .legal_moves()  — list of directions that would change the board
        .move(direction) — apply move + spawn tile; returns True if board changed
        .clone()        — deep copy for search (no history, independent RNG)

    Directions: 'up', 'down', 'left', 'right'
"""

import copy
import os
import random


class Game2048:
    def __init__(self, size=None, seed=None):
        if size is None:
            size = int(os.environ.get('BOARD_SIZE', '4'))
        self.size = size
        self._rng = random.Random(seed)
        self.board = [[0] * size for _ in range(size)]
        self.score = 0
        self.move_count = 0
        self.history = []

        # Spawn two initial tiles
        self._spawn_tile()
        self._spawn_tile()

        # Record initial state
        self.history.append((self._snapshot(), self.score, None))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_over(self):
        return len(self.legal_moves()) == 0

    @property
    def max_tile(self):
        return max(cell for row in self.board for cell in row)

    def get_board(self):
        """Return a deep copy of the board."""
        return [row[:] for row in self.board]

    def legal_moves(self):
        """Return list of directions that would actually change the board."""
        moves = []
        for direction in ('up', 'down', 'left', 'right'):
            if self._would_change(direction):
                moves.append(direction)
        return moves

    def move(self, direction):
        """Apply a move. Returns True if the board changed."""
        if direction not in ('up', 'down', 'left', 'right'):
            raise ValueError(f"Invalid direction: {direction}")

        old = self._snapshot()
        old_score = self.score

        if direction == 'left':
            self._slide_left()
        elif direction == 'right':
            self._slide_right()
        elif direction == 'up':
            self._slide_up()
        elif direction == 'down':
            self._slide_down()

        if self.board == old:
            return False

        self._spawn_tile()
        self.move_count += 1
        self.history.append((self._snapshot(), self.score, direction))
        return True

    def clone(self):
        """Deep copy for search simulation — no history, independent RNG."""
        c = Game2048.__new__(Game2048)
        c.size = self.size
        c.board = [row[:] for row in self.board]
        c.score = self.score
        c.move_count = self.move_count
        c.history = []
        # Copy RNG state so clone diverges independently
        c._rng = random.Random()
        c._rng.setstate(self._rng.getstate())
        return c

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _snapshot(self):
        return [row[:] for row in self.board]

    def _spawn_tile(self):
        """Place a 2 (90%) or 4 (10%) on a random empty cell."""
        empty = [(r, c) for r in range(self.size)
                 for c in range(self.size) if self.board[r][c] == 0]
        if not empty:
            return
        r, c = self._rng.choice(empty)
        self.board[r][c] = 2 if self._rng.random() < 0.9 else 4

    def _slide_row_left(self, row):
        """Slide and merge a single row to the left. Returns (new_row, points)."""
        # Remove zeros
        tiles = [v for v in row if v != 0]
        merged = []
        points = 0
        i = 0
        while i < len(tiles):
            if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
                val = tiles[i] * 2
                merged.append(val)
                points += val
                i += 2
            else:
                merged.append(tiles[i])
                i += 1
        # Pad with zeros
        merged.extend([0] * (self.size - len(merged)))
        return merged, points

    def _slide_left(self):
        for r in range(self.size):
            self.board[r], pts = self._slide_row_left(self.board[r])
            self.score += pts

    def _slide_right(self):
        for r in range(self.size):
            row_rev = self.board[r][::-1]
            merged, pts = self._slide_row_left(row_rev)
            self.board[r] = merged[::-1]
            self.score += pts

    def _slide_up(self):
        for c in range(self.size):
            col = [self.board[r][c] for r in range(self.size)]
            merged, pts = self._slide_row_left(col)
            for r in range(self.size):
                self.board[r][c] = merged[r]
            self.score += pts

    def _slide_down(self):
        for c in range(self.size):
            col = [self.board[r][c] for r in range(self.size - 1, -1, -1)]
            merged, pts = self._slide_row_left(col)
            for i, r in enumerate(range(self.size - 1, -1, -1)):
                self.board[r][c] = merged[i]
            self.score += pts

    def _would_change(self, direction):
        """Check if a move would change the board without modifying state."""
        saved = self._snapshot()
        saved_score = self.score
        if direction == 'left':
            self._slide_left()
        elif direction == 'right':
            self._slide_right()
        elif direction == 'up':
            self._slide_up()
        elif direction == 'down':
            self._slide_down()
        changed = self.board != saved
        self.board = saved
        self.score = saved_score
        return changed
