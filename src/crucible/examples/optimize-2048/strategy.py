"""2048 strategy — this is the file the agent optimizes.

Baseline: random move from legal moves.
"""

import random


def choose_move(board, legal_moves, score):
    """Choose next direction for the 2048 board.

    Args:
        board:       list[list[int]], current board state (0 = empty)
        legal_moves: list[str], valid directions ('up','down','left','right')
        score:       int, current game score

    Returns:
        direction: one of 'up', 'down', 'left', 'right'
    """
    return random.choice(legal_moves)
