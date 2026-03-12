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
