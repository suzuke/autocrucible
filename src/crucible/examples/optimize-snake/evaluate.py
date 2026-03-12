"""Evaluation harness for Snake agent — DO NOT MODIFY.

Runs 30 games with fixed seeds. Measures avg_score, avg_food_eaten,
avg_steps_survived.

Output format (parsed by crucible):
    avg_score:        <float>   (primary metric = food*10 + steps*0.1)
    avg_food_eaten:   <float>
    avg_steps:        <float>
    games_played:     <int>
"""

import random

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
            moves = game.legal_moves()
            direction = random.choice(moves) if moves else 'UP'

        # If direction is illegal, pick a random legal move instead
        legal = game.legal_moves()
        if direction not in legal:
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
