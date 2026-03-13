"""Evaluation harness for policy.py — DO NOT MODIFY.

Implements CartPole physics from scratch (no gym dependency).
Runs 200 episodes and computes mean steps survived.

Physics parameters match OpenAI Gym CartPole-v1.

Output format (parsed by crucible):
    mean_steps: <float>
    min_steps: <int>
    max_steps: <int>
    episodes: <int>
    perfect_episodes: <int>
"""

import math
import random
import sys
import traceback

# CartPole physics constants (from OpenAI Gym CartPole-v1)
GRAVITY = 9.8
MASS_CART = 1.0
MASS_POLE = 0.1
TOTAL_MASS = MASS_CART + MASS_POLE
POLE_HALF_LENGTH = 0.5
POLE_MASS_LENGTH = MASS_POLE * POLE_HALF_LENGTH
FORCE_MAG = 10.0
TAU = 0.02  # seconds per step

X_THRESHOLD = 2.4
ANGLE_THRESHOLD_RAD = 12 * math.pi / 180  # 12 degrees

MAX_STEPS = 500
N_EPISODES = 200
SEED = 777


def cartpole_step(state, action):
    """Advance CartPole physics by one timestep. Returns (new_state, done)."""
    x, x_dot, theta, theta_dot = state
    force = FORCE_MAG if action == 1 else -FORCE_MAG

    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)

    temp = (force + POLE_MASS_LENGTH * theta_dot ** 2 * sin_theta) / TOTAL_MASS
    theta_acc = (GRAVITY * sin_theta - cos_theta * temp) / (
        POLE_HALF_LENGTH * (4.0 / 3.0 - MASS_POLE * cos_theta ** 2 / TOTAL_MASS)
    )
    x_acc = temp - POLE_MASS_LENGTH * theta_acc * cos_theta / TOTAL_MASS

    x = x + TAU * x_dot
    x_dot = x_dot + TAU * x_acc
    theta = theta + TAU * theta_dot
    theta_dot = theta_dot + TAU * theta_acc

    done = bool(
        abs(x) > X_THRESHOLD
        or abs(theta) > ANGLE_THRESHOLD_RAD
    )
    return [x, x_dot, theta, theta_dot], done


def run_episode(policy_fn, episode_seed):
    """Run one CartPole episode. Returns steps survived."""
    ep_rng = random.Random(episode_seed)
    state = [ep_rng.uniform(-0.05, 0.05) for _ in range(4)]

    for step in range(MAX_STEPS):
        action = policy_fn(list(state))
        if action not in (0, 1):
            return 0  # invalid action
        state, done = cartpole_step(state, action)
        if done:
            return step + 1

    return MAX_STEPS


def main():
    try:
        from policy import select_action

        rng = random.Random(SEED)
        episode_seeds = [rng.randint(0, 10**9) for _ in range(N_EPISODES)]

        steps_list = []
        for seed in episode_seeds:
            steps = run_episode(select_action, seed)
            steps_list.append(steps)

        mean_steps = sum(steps_list) / len(steps_list)
        print(f"mean_steps: {mean_steps:.2f}")
        print(f"min_steps: {min(steps_list)}")
        print(f"max_steps: {max(steps_list)}")
        print(f"episodes: {N_EPISODES}")
        print(f"perfect_episodes: {sum(1 for s in steps_list if s >= MAX_STEPS)}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("mean_steps: 0.0")


if __name__ == "__main__":
    main()
