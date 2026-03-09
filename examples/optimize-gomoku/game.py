"""Gomoku (五子棋) game engine — DO NOT MODIFY.

9x9 board, first to connect 5 stones wins.
Provides game state management, move validation, and win detection.
"""

import numpy as np

BOARD_SIZE = 9
WIN_LENGTH = 5

# Directions for checking connections: horizontal, vertical, 2 diagonals
DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]


class GomokuGame:
    """Gomoku game state."""

    def __init__(self):
        # 0 = empty, 1 = black, -1 = white
        self.board = np.zeros((BOARD_SIZE, BOARD_SIZE), dtype=np.int8)
        self.current_player = 1  # black goes first
        self.history = []
        self.winner = None
        self.done = False

    def copy(self):
        g = GomokuGame()
        g.board = self.board.copy()
        g.current_player = self.current_player
        g.history = list(self.history)
        g.winner = self.winner
        g.done = self.done
        return g

    def legal_moves(self):
        """Return list of (row, col) for empty positions."""
        if self.done:
            return []
        positions = np.argwhere(self.board == 0)
        return [tuple(p) for p in positions]

    def legal_moves_mask(self):
        """Return flat boolean mask of legal moves (BOARD_SIZE^2,)."""
        return (self.board.flatten() == 0).astype(np.float32)

    def play(self, row, col):
        """Place a stone. Returns True if move was valid."""
        if self.done or self.board[row, col] != 0:
            return False
        self.board[row, col] = self.current_player
        self.history.append((row, col))
        if self._check_win(row, col):
            self.winner = self.current_player
            self.done = True
        elif len(self.history) == BOARD_SIZE * BOARD_SIZE:
            self.done = True  # draw
        else:
            self.current_player *= -1
        return True

    def _check_win(self, row, col):
        """Check if the last move at (row, col) creates a line of 5."""
        player = self.board[row, col]
        for dr, dc in DIRECTIONS:
            count = 1
            for sign in (1, -1):
                r, c = row + sign * dr, col + sign * dc
                while (
                    0 <= r < BOARD_SIZE
                    and 0 <= c < BOARD_SIZE
                    and self.board[r, c] == player
                ):
                    count += 1
                    r += sign * dr
                    c += sign * dc
            if count >= WIN_LENGTH:
                return True
        return False

    def encode(self):
        """Encode board as neural network input.

        Returns: np.array of shape (3, BOARD_SIZE, BOARD_SIZE)
            Channel 0: current player's stones
            Channel 1: opponent's stones
            Channel 2: color indicator (all 1s if black to play, all 0s if white)
        """
        state = np.zeros((3, BOARD_SIZE, BOARD_SIZE), dtype=np.float32)
        state[0] = (self.board == self.current_player).astype(np.float32)
        state[1] = (self.board == -self.current_player).astype(np.float32)
        if self.current_player == 1:
            state[2] = 1.0
        return state

    def action_to_coord(self, action):
        """Convert flat action index to (row, col)."""
        return (action // BOARD_SIZE, action % BOARD_SIZE)

    def coord_to_action(self, row, col):
        """Convert (row, col) to flat action index."""
        return row * BOARD_SIZE + col


class RandomPlayer:
    """Baseline: uniformly random legal moves."""

    def __init__(self, seed=None):
        self.rng = np.random.RandomState(seed)

    def choose_move(self, game):
        moves = game.legal_moves()
        if not moves:
            return None
        idx = self.rng.randint(len(moves))
        return moves[idx]


class GreedyPlayer:
    """Baseline: plays to extend own lines or block opponent's longest line."""

    def __init__(self, seed=None):
        self.rng = np.random.RandomState(seed)

    def choose_move(self, game):
        moves = game.legal_moves()
        if not moves:
            return None

        best_score = -1
        best_moves = []

        for r, c in moves:
            score = self._evaluate_move(game, r, c)
            if score > best_score:
                best_score = score
                best_moves = [(r, c)]
            elif score == best_score:
                best_moves.append((r, c))

        idx = self.rng.randint(len(best_moves))
        return best_moves[idx]

    def _evaluate_move(self, game, row, col):
        player = game.current_player
        score = 0

        # Check how this move extends our lines or blocks opponent
        for dr, dc in DIRECTIONS:
            own = self._count_line(game.board, row, col, dr, dc, player)
            opp = self._count_line(game.board, row, col, dr, dc, -player)
            # Winning move
            if own >= WIN_LENGTH - 1:
                return 10000
            # Block opponent win
            if opp >= WIN_LENGTH - 1:
                score = max(score, 5000)
            score = max(score, own * 10 + opp * 5)

        return score

    def _count_line(self, board, row, col, dr, dc, player):
        count = 0
        for sign in (1, -1):
            r, c = row + sign * dr, col + sign * dc
            while (
                0 <= r < BOARD_SIZE
                and 0 <= c < BOARD_SIZE
                and board[r, c] == player
            ):
                count += 1
                r += sign * dr
                c += sign * dc
        return count
