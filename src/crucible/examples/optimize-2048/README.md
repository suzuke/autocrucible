# optimize-2048

Optimize a 2048 game-playing AI to maximize average score across 20 seeded games.

**Requirements**: None (pure Python)

## What It Does

- Agent edits `strategy.py` to implement `choose_move(board, legal_moves, score)` for the 2048 game
- Evaluation plays 20 games with fixed seeds and reports average score plus diagnostics (monotonicity, empty cells, tile distribution)
- Strategies range from random (~1,000) to expectimax with tuned heuristics (~50,000+)

## Quick Start

```bash
crucible new my-2048 -e optimize-2048
cd my-2048
crucible run --tag v1
```

## Metrics

- **Metric**: avg_score (maximize)
- **Baseline**: ~1,000 (random moves)
- **Eval time**: ~10-60s depending on strategy complexity (300s timeout)
