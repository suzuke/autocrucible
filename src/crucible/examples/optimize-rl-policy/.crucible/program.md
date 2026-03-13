# CartPole Policy Optimization

You are designing a policy to balance a pole on a moving cart.

## Goal

Maximize `mean_steps` — average steps survived across 200 episodes.
Maximum possible: 500 steps per episode.
Baseline (random policy): ~20 steps.

## Interface

```python
def select_action(obs: list[float]) -> int:
    # obs = [cart_position, cart_velocity, pole_angle, pole_angular_vel]
    # Return 0 (push LEFT) or 1 (push RIGHT)
```

## Rules

- Edit only `policy.py`
- No gym, torch, or numpy — pure Python + `math` and `random` stdlib
- Return must be 0 or 1
- Policy should be deterministic (no external state changes between episodes)

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `policy.py`

## Observation Space

See `obs_info.txt` for full details on observations and physics.

## Strategy

Start simple — follow the pole angle:
```python
return 1 if obs[2] > 0 else 0
```
This gets ~150 steps. To reach 400+, combine angle AND angular velocity (obs[3]).
A weighted sum `w2 * obs[2] + w3 * obs[3] > 0` can reach 400+ steps.
Adding cart position (obs[0]) and velocity (obs[1]) can push further still.
