# Gomoku AlphaZero Agent Optimization

You are optimizing an AlphaZero-style Gomoku (五子棋) agent on a 9×9 board.

## Goal

Maximize `win_rate` — weighted win rate against baseline opponents (30% vs Random + 70% vs Greedy). Range: 0–100.

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- Edit only `agent.py`
- You may use: `torch`, `numpy`, and Python standard library

## Interface Contract

`agent.py` must export:
- `train(time_budget_sec) -> (net, device)` — train within the given time budget (300s)
- `choose_move(game, net, device) -> (row, col)` — return a valid move
- `GomokuNet` — the neural network class
- `MODEL_PATH` — path to the saved model

## Opponents

- **Random**: picks a random legal move
- **Greedy**: picks the move that maximizes a simple heuristic (harder)

Weighted score: 30% vs Random + 70% vs Greedy.

## Game Rules

Gomoku is played on a 9×9 board. Two players alternate placing stones. The first player to get 5 in a row (horizontal, vertical, or diagonal) wins. The game engine is in `game.py` (read-only). The evaluation harness is in `evaluate.py` (read-only).

## Persistent Storage

The `artifacts/` directory survives across iterations (not affected by revert).
Use it to save model checkpoints (`artifacts/model.pt`) so you can resume
training from previous iterations instead of starting from scratch each time.

## Context

The baseline implements AlphaZero: a dual-headed neural network (policy + value), Monte Carlo Tree Search guided by the network, and self-play training. The 300s training budget is tight — use `artifacts/` to accumulate training across iterations. The evaluation uses 50 MCTS simulations per move.
