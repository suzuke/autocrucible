# optimize-gomoku

Optimize an AlphaZero-style Gomoku (five-in-a-row) agent on a 9x9 board.

**Requirements**: PyTorch, numpy

## What It Does

- Agent edits `agent.py` to implement `train(time_budget_sec)` and `choose_move(game, net, device)` for a neural network Gomoku player
- Training uses self-play with MCTS guided by a dual-headed network (policy + value), with a 300s budget
- Evaluation measures weighted win rate: 30% vs Random + 70% vs Greedy opponent

## Quick Start

```bash
crucible new my-gomoku -e optimize-gomoku
cd my-gomoku
crucible run --tag v1
```

## Metrics

- **Metric**: win_rate (maximize) -- range 0-100
- **Baseline**: ~0 (untrained network)
- **Eval time**: ~5-10 min per iteration (300s training + evaluation, 600s timeout)
