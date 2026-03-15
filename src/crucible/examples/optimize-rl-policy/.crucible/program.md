# Pendulum Swing-Up Optimization

You are writing a controller for a pendulum that must swing from hanging to upright
and balance there as long as possible.

## Goal

Maximize `mean_reward` — average of cos(theta) across 200 episodes × 400 steps.

- cos(0) = +1.0 → pole perfectly upright
- cos(±pi) = −1.0 → pole hanging downward

Baseline (random policy): ~−0.95
Target to beat: +0.60

## Interface

```python
def select_action(obs: list[float]) -> int:
    # obs = [theta, theta_dot]
    # theta: angle in radians. 0 = upright, ±pi = hanging
    # theta_dot: angular velocity (rad/s)
    # Return 0 (negative torque / clockwise) or 1 (positive torque / counterclockwise)
```

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `policy.py`
- Return must be exactly 0 or 1

## Soft Rules

- No gym, torch, or numpy — pure Python + `math` and `random` stdlib
- Policy may maintain internal state (e.g., `_prev_theta = None`) for tracking

## What You Can Try

**Phase 1 — Swing-up (energy pumping)**:
Push in the direction of current angular velocity to build energy:
```python
return 1 if obs[1] > 0 else 0   # push same direction as velocity
```
This alone gets ~−0.20. Not enough to hold the top, but a good start.

**Phase 2 — Balance (stabilization)**:
When near upright (small |theta|), oppose the tilt with negative feedback:
```python
# theta > 0 (tilted left) → need clockwise torque → action=0
return 0 if (k1 * obs[0] + k2 * obs[1]) > 0 else 1
```

**Combined**: Switch from energy pumping to balance when close to upright.
```python
if abs(obs[0]) < SWITCH_ANGLE and abs(obs[1]) < SWITCH_OMEGA:
    return 0 if (k1 * obs[0] + k2 * obs[1]) > 0 else 1  # balance
else:
    return 1 if obs[1] > 0 else 0  # energy pump
```

**Energy-based switching**: More principled — compute total mechanical energy
`E = 0.5 * omega² + 9.8 * cos(theta)` and switch modes based on E vs target (9.8).

**Tip**: Baseline runs in ~3s. You have a 60s budget. Try 2-3 ideas per iteration.

## Observation Reference

See `obs_info.txt` for full physics, constants, and performance benchmarks.
