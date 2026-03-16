"""Evaluation harness for 2048 strategy — DO NOT MODIFY.

Runs 20 games with fixed seeds (0-19). Measures avg_score as the primary
metric, plus several sub-metrics for diagnostics.

Output format (parsed by crucible):
    avg_score:          <float>   (primary metric)

Sub-metrics (visible to agent via run.log):
    max_tile_mode:      <int>     most common highest tile across games
    tile_distribution:  <str>     e.g. "2048:5, 1024:20, 512:25"
    avg_moves:          <float>   average move count per game
    avg_monotonicity:   <float>   board ordering quality (0-1)
    avg_empty_cells:    <float>   average empty cells per move
    avg_move_time_ms:   <float>   average compute time per choose_move call
    best_game:          seed=<int> score=<int>
    worst_game:         seed=<int> score=<int>

Also saves replay.json with best and worst game histories.
"""

import json
import time
from collections import Counter

from game import Game2048

NUM_GAMES = 20


def compute_monotonicity(board):
    """Compute monotonicity score for a board state.

    For each row and column, check if it's increasing or decreasing,
    take the better direction. Normalize to [0, 1].
    """
    size = len(board)
    if size == 0:
        return 0.0

    total_score = 0.0
    total_pairs = 0

    # Check rows
    for row in board:
        inc = 0
        dec = 0
        for i in range(len(row) - 1):
            if row[i] <= row[i + 1]:
                inc += 1
            if row[i] >= row[i + 1]:
                dec += 1
        total_score += max(inc, dec)
        total_pairs += len(row) - 1

    # Check columns
    for c in range(size):
        inc = 0
        dec = 0
        for r in range(size - 1):
            if board[r][c] <= board[r + 1][c]:
                inc += 1
            if board[r][c] >= board[r + 1][c]:
                dec += 1
        total_score += max(inc, dec)
        total_pairs += size - 1

    return total_score / total_pairs if total_pairs > 0 else 0.0


def count_empty(board):
    """Count empty cells on the board."""
    return sum(1 for row in board for cell in row if cell == 0)


def run_game(seed, choose_move):
    """Run one game. Returns dict with game results and history."""
    game = Game2048(seed=seed)

    total_mono = 0.0
    total_empty = 0.0
    total_time = 0.0
    move_samples = 0

    while not game.is_over:
        board = game.get_board()
        legal = game.legal_moves()

        # Measure monotonicity and empty cells at each move
        total_mono += compute_monotonicity(board)
        total_empty += count_empty(board)
        move_samples += 1

        # Time the strategy call
        t0 = time.perf_counter()
        try:
            direction = choose_move(board, legal, game.score)
        except Exception:
            direction = legal[0]
        elapsed = time.perf_counter() - t0
        total_time += elapsed

        # Validate move
        if direction not in legal:
            direction = legal[0]

        game.move(direction)

    return {
        "seed": seed,
        "score": game.score,
        "max_tile": game.max_tile,
        "moves": game.move_count,
        "avg_mono": total_mono / move_samples if move_samples > 0 else 0.0,
        "avg_empty": total_empty / move_samples if move_samples > 0 else 0.0,
        "total_time": total_time,
        "move_samples": move_samples,
        "history": game.history,
        "board_size": game.size,
    }


def main():
    # Import strategy
    try:
        from strategy import choose_move
    except Exception as e:
        print(f"ERROR importing strategy: {e}")
        print("avg_score: 0.00")
        return

    results = []
    for seed in range(NUM_GAMES):
        results.append(run_game(seed, choose_move))

    # Primary metric
    scores = [r["score"] for r in results]
    avg_score = sum(scores) / len(scores)

    # Max tile distribution
    tile_counts = Counter(r["max_tile"] for r in results)
    max_tile_mode = tile_counts.most_common(1)[0][0]
    tile_dist = ", ".join(
        f"{tile}:{count}"
        for tile, count in sorted(tile_counts.items(), reverse=True)
    )

    # Averages
    avg_moves = sum(r["moves"] for r in results) / len(results)
    avg_mono = sum(r["avg_mono"] for r in results) / len(results)
    avg_empty = sum(r["avg_empty"] for r in results) / len(results)

    total_move_samples = sum(r["move_samples"] for r in results)
    total_time = sum(r["total_time"] for r in results)
    avg_move_time_ms = (total_time / total_move_samples * 1000) if total_move_samples > 0 else 0.0

    # Best and worst games
    best = max(results, key=lambda r: r["score"])
    worst = min(results, key=lambda r: r["score"])

    # Print metrics
    print(f"max_tile_mode: {max_tile_mode}")
    print(f"tile_distribution: {tile_dist}")
    print(f"avg_moves: {avg_moves:.2f}")
    print(f"avg_monotonicity: {avg_mono:.4f}")
    print(f"avg_empty_cells: {avg_empty:.2f}")
    print(f"avg_move_time_ms: {avg_move_time_ms:.4f}")
    print(f"best_game: seed={best['seed']} score={best['score']}")
    print(f"worst_game: seed={worst['seed']} score={worst['score']}")
    print(f"avg_score: {avg_score:.2f}")

    # Save replay.json
    board_size = results[0]["board_size"]

    def history_to_moves(history):
        return [
            {"board": board, "score": score, "move": move}
            for board, score, move in history
        ]

    replay = {
        "board_size": board_size,
        "best": {
            "seed": best["seed"],
            "score": best["score"],
            "moves": history_to_moves(best["history"]),
        },
        "worst": {
            "seed": worst["seed"],
            "score": worst["score"],
            "moves": history_to_moves(worst["history"]),
        },
    }

    with open("replay.json", "w") as f:
        json.dump(replay, f, indent=2)


if __name__ == "__main__":
    main()
