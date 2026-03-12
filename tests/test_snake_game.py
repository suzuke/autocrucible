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
    import collections
    g.snake = collections.deque([(0, 0)])
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
    import collections
    g.snake = collections.deque([(0, 0)])
    g.occupied = {(0, 0)}
    g.step('UP')
    assert g.done


def test_step_self_collision_ends_game():
    import collections
    g = SnakeGame(seed=0)
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
    # Either food moved or game is done
    assert g.food != (r + 1, c) or g.done


def test_step_limit():
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
