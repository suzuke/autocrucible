# optimize-rl-policy

Write a controller for a pendulum swing-up and balance task using only pure Python.

**Requirements**: None (pure Python + math stdlib)

## What It Does

- Agent edits `policy.py` to implement `select_action(obs) -> int` where obs is `[theta, theta_dot]` and action is 0 (clockwise) or 1 (counterclockwise)
- The pendulum starts hanging down and must swing up to vertical and balance there
- Evaluation averages cos(theta) over 200 episodes x 400 steps (+1.0 = upright, -1.0 = hanging)

## Quick Start

```bash
crucible new my-rl-policy -e optimize-rl-policy
cd my-rl-policy
crucible run --tag v1
```

## Metrics

- **Metric**: mean_reward (maximize) -- average cos(theta), range -1.0 to +1.0
- **Baseline**: ~-0.95 (random policy)
- **Eval time**: ~3-10s (60s timeout)
