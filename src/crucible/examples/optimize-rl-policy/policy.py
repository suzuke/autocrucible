"""Pendulum swing-up policy — edit this file to improve mean_reward.

Interface:
  select_action(obs: list[float]) -> int
      obs = [theta, theta_dot]
          theta:     pole angle (radians). 0 = upright, ±pi = hanging
          theta_dot: angular velocity (radians/second)
      Return 0 (negative/clockwise torque) or 1 (positive/counterclockwise torque)

Goal: maximize mean cos(theta) across 200 episodes.
Baseline (random): ~−0.95. Target: > +0.60.

Constraints:
  - No gym, torch, or numpy (pure Python + math/random stdlib)
  - Return must be 0 or 1
  - Policy may use module-level state (e.g., prev_theta) to track history
"""

import random as _random

_rng = _random.Random(99)


def select_action(obs: list[float]) -> int:
    """Baseline: random action."""
    return _rng.randint(0, 1)
