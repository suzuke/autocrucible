"""Evaluation harness for Gomoku AlphaZero agent — DO NOT MODIFY.

Trains the agent within a time budget, then plays games against
baseline opponents (Random and Greedy) to measure win rate.

Output format (parsed by crucible):
    win_rate: <float>      (primary metric, 0-100)
    vs_random: <float>     (win rate vs random, 0-100)
    vs_greedy: <float>     (win rate vs greedy, 0-100)
    training_ok: <bool>
"""

import os
import sys
import time
import traceback

import numpy as np
import torch

from game import GomokuGame, RandomPlayer, GreedyPlayer

SEED = 42

# Detect accelerator
if torch.cuda.is_available():
    _ACCEL = "cuda"
elif torch.backends.mps.is_available():
    _ACCEL = "mps"
else:
    _ACCEL = "cpu"

# Adjust eval game count based on hardware speed
if _ACCEL == "cuda":
    EVAL_GAMES_PER_OPPONENT = 20
elif _ACCEL == "mps":
    EVAL_GAMES_PER_OPPONENT = 15
else:
    EVAL_GAMES_PER_OPPONENT = 10

TRAIN_TIME_BUDGET = 300  # seconds for training
EVAL_MCTS_SIMS = 50  # fewer sims during eval for speed

print(f"device: {_ACCEL}")
print(f"eval_games_per_opponent: {EVAL_GAMES_PER_OPPONENT}")


def play_game(agent_choose_fn, opponent, agent_plays_black=True, seed=None):
    """Play a single game. Returns 1 if agent wins, 0 for draw, -1 if agent loses."""
    game = GomokuGame()

    while not game.done:
        is_agent_turn = (game.current_player == 1) == agent_plays_black

        if is_agent_turn:
            move = agent_choose_fn(game)
        else:
            move = opponent.choose_move(game)

        if move is None:
            break
        game.play(move[0], move[1])

    if game.winner is None:
        return 0
    agent_color = 1 if agent_plays_black else -1
    return 1 if game.winner == agent_color else -1


def evaluate_agent(agent_choose_fn, seed=SEED):
    """Play games against baselines. Returns metrics dict."""
    rng = np.random.RandomState(seed)
    results = {}

    for name, OpponentClass in [("random", RandomPlayer), ("greedy", GreedyPlayer)]:
        wins = 0
        total = 0

        for i in range(EVAL_GAMES_PER_OPPONENT):
            opponent_seed = rng.randint(0, 2**31)
            opponent = OpponentClass(seed=opponent_seed)

            # Agent plays black
            result = play_game(agent_choose_fn, opponent, agent_plays_black=True, seed=opponent_seed)
            if result == 1:
                wins += 1
            total += 1

            # Agent plays white
            opponent = OpponentClass(seed=opponent_seed + 1)
            result = play_game(agent_choose_fn, opponent, agent_plays_black=False, seed=opponent_seed + 1)
            if result == 1:
                wins += 1
            total += 1

        results[name] = wins / total * 100 if total > 0 else 0.0

    # Overall win rate is weighted average (greedy counts more)
    overall = results["random"] * 0.3 + results["greedy"] * 0.7
    results["overall"] = overall
    return results


def main():
    np.random.seed(SEED)
    torch.manual_seed(SEED)

    training_ok = False
    try:
        from agent import train, choose_move, GomokuNet, MODEL_PATH

        # Phase 1: Train
        print("=== Training Phase ===")
        t0 = time.time()
        net, device = train(time_budget_sec=TRAIN_TIME_BUDGET)
        train_time = time.time() - t0
        print(f"train_time_sec: {train_time:.1f}")
        training_ok = True

    except Exception as e:
        print(f"Training failed: {e}")
        traceback.print_exc()
        print("win_rate: 0.0")
        print("vs_random: 0.0")
        print("vs_greedy: 0.0")
        print("training_ok: false")
        return

    if not training_ok:
        print("win_rate: 0.0")
        print("vs_random: 0.0")
        print("vs_greedy: 0.0")
        print("training_ok: false")
        return

    # Phase 2: Evaluate
    print("\n=== Evaluation Phase ===")

    def agent_choose_fn(game):
        return choose_move(game, net, device)

    try:
        results = evaluate_agent(agent_choose_fn)
        print(f"\nvs_random: {results['random']:.1f}")
        print(f"vs_greedy: {results['greedy']:.1f}")
        print(f"win_rate: {results['overall']:.1f}")
        print("training_ok: true")

    except Exception as e:
        print(f"Evaluation failed: {e}")
        traceback.print_exc()
        print("win_rate: 0.0")
        print("vs_random: 0.0")
        print("vs_greedy: 0.0")
        print("training_ok: false")


if __name__ == "__main__":
    main()
