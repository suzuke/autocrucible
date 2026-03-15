"""Evaluation harness for policy.py — DO NOT MODIFY.

Implements a pendulum swing-up task from scratch (no gym dependency).
The pendulum starts hanging downward and must be swung up and held upright.

Observation:  [theta, theta_dot]
  theta = 0    → upright (goal)
  theta = ±pi  → hanging (start region)
  theta_dot    → angular velocity (rad/s)

Action: 0 = apply negative torque, 1 = apply positive torque

Metric: mean_reward = average of cos(theta) across all steps and all episodes.
  cos(0)   = +1.0  (upright — best)
  cos(±pi) = -1.0  (hanging — worst)

Output format (parsed by crucible):
    mean_reward: <float>
    upright_fraction: <float>
    episodes: <int>
"""

import math
import random
import traceback

# Pendulum physics constants
G = 9.8       # gravity (m/s²)
L = 1.0       # pole length (m)
M = 1.0       # pole mass (kg)
MAX_TORQUE = 2.0   # Nm
MAX_OMEGA = 8.0    # rad/s (velocity clip)
DT = 0.05          # seconds per step

MAX_STEPS = 400    # steps per episode (20 seconds)
N_EPISODES = 200
SEED = 777


def angle_wrap(theta):
    """Wrap angle to (-pi, pi]."""
    return ((theta + math.pi) % (2 * math.pi)) - math.pi


def pendulum_step(theta, omega, action):
    """Advance pendulum physics. Returns (new_theta, new_omega)."""
    torque = MAX_TORQUE if action == 1 else -MAX_TORQUE

    # theta=0 is upright (unstable); gravity destabilizes: alpha_gravity = (g/l)*sin(theta)
    # Positive torque (action=1) applies counterclockwise angular acceleration
    alpha = (G / L) * math.sin(theta) + torque / (M * L ** 2)

    omega = max(-MAX_OMEGA, min(MAX_OMEGA, omega + DT * alpha))
    theta = angle_wrap(theta + DT * omega)
    return theta, omega


def run_episode(policy_fn, episode_seed):
    """Run one pendulum episode. Returns (mean_cos_theta, upright_steps)."""
    ep_rng = random.Random(episode_seed)

    # Start near hanging position (theta near ±pi)
    theta = ep_rng.uniform(math.pi - 0.3, math.pi + 0.3)
    theta = angle_wrap(theta)
    omega = ep_rng.uniform(-0.5, 0.5)

    cos_sum = 0.0
    upright_steps = 0

    for step in range(MAX_STEPS):
        cos_theta = math.cos(theta)
        cos_sum += cos_theta
        if cos_theta > 0.95:  # within ~18° of upright
            upright_steps += 1

        action = policy_fn([theta, omega])
        if action not in (0, 1):
            # Invalid action: penalize rest of episode
            cos_sum += -1.0 * (MAX_STEPS - step - 1)
            break

        theta, omega = pendulum_step(theta, omega, action)

    mean_cos = cos_sum / MAX_STEPS
    return mean_cos, upright_steps


def main():
    try:
        from policy import select_action

        rng = random.Random(SEED)
        episode_seeds = [rng.randint(0, 10**9) for _ in range(N_EPISODES)]

        rewards = []
        total_upright = 0
        for seed in episode_seeds:
            r, up = run_episode(select_action, seed)
            rewards.append(r)
            total_upright += up

        mean_reward = sum(rewards) / len(rewards)
        upright_fraction = total_upright / (N_EPISODES * MAX_STEPS)

        print(f"mean_reward: {mean_reward:.4f}")
        print(f"upright_fraction: {upright_fraction:.4f}")
        print(f"episodes: {N_EPISODES}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("mean_reward: -1.0")


if __name__ == "__main__":
    main()
