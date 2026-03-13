"""CartPole policy — edit this file to improve mean_steps.

Interface:
  select_action(obs: list[float]) -> int
      obs = [cart_position, cart_velocity, pole_angle, pole_angular_vel]
      Return 0 (push left) or 1 (push right).

Goal: maximize mean episode length across 200 episodes.
Baseline (random): ~20 steps. Good policy: 400-500 steps.

Constraints:
  - No gym, torch, or numpy imports (pure Python + math/random stdlib)
  - Policy should be deterministic (reproducible given same obs)
"""

import random as _random

_rng = _random.Random(99)


def select_action(obs: list[float]) -> int:
    """Baseline: random action."""
    return _rng.randint(0, 1)
