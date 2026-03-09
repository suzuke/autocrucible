"""AlphaZero-style Gomoku agent — this is the file the agent optimizes.

Components:
  - Neural network (policy + value heads)
  - Monte Carlo Tree Search (MCTS)
  - Self-play training loop

The agent should optimize: network architecture, MCTS parameters,
training hyperparameters, and self-play strategy.
"""

import math
import os

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

from game import BOARD_SIZE, GomokuGame

ACTION_SIZE = BOARD_SIZE * BOARD_SIZE
MODEL_PATH = "model.pt"

# ── Hyperparameters ──────────────────────────────────────────────────────────

NUM_CHANNELS = 64
NUM_RES_BLOCKS = 4
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 1e-4
BATCH_SIZE = 64
TRAIN_EPOCHS = 5

MCTS_SIMULATIONS = 100
C_PUCT = 1.4
TEMPERATURE = 1.0
TEMPERATURE_DROP_MOVE = 10
DIRICHLET_ALPHA = 0.3
DIRICHLET_EPSILON = 0.25

SELF_PLAY_GAMES = 50
REPLAY_BUFFER_SIZE = 10000


# ── Device Detection ─────────────────────────────────────────────────────────


def get_device():
    """Auto-detect best available device: CUDA > MPS > CPU."""
    if torch.cuda.is_available():
        return torch.device("cuda")
    elif torch.backends.mps.is_available():
        return torch.device("mps")
    else:
        return torch.device("cpu")


# ── Neural Network ───────────────────────────────────────────────────────────


class ResBlock(nn.Module):
    def __init__(self, channels):
        super().__init__()
        self.conv1 = nn.Conv2d(channels, channels, 3, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(channels)
        self.conv2 = nn.Conv2d(channels, channels, 3, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(channels)

    def forward(self, x):
        residual = x
        x = F.relu(self.bn1(self.conv1(x)))
        x = self.bn2(self.conv2(x))
        x = F.relu(x + residual)
        return x


class GomokuNet(nn.Module):
    """Dual-headed network: policy (move probabilities) + value (position eval)."""

    def __init__(self):
        super().__init__()
        # Input: 3 channels (own stones, opponent stones, color indicator)
        self.conv_in = nn.Sequential(
            nn.Conv2d(3, NUM_CHANNELS, 3, padding=1, bias=False),
            nn.BatchNorm2d(NUM_CHANNELS),
            nn.ReLU(),
        )
        self.res_blocks = nn.Sequential(
            *[ResBlock(NUM_CHANNELS) for _ in range(NUM_RES_BLOCKS)]
        )

        # Policy head
        self.policy_head = nn.Sequential(
            nn.Conv2d(NUM_CHANNELS, 2, 1, bias=False),
            nn.BatchNorm2d(2),
            nn.ReLU(),
            nn.Flatten(),
            nn.Linear(2 * BOARD_SIZE * BOARD_SIZE, ACTION_SIZE),
        )

        # Value head
        self.value_head = nn.Sequential(
            nn.Conv2d(NUM_CHANNELS, 1, 1, bias=False),
            nn.BatchNorm2d(1),
            nn.ReLU(),
            nn.Flatten(),
            nn.Linear(BOARD_SIZE * BOARD_SIZE, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
            nn.Tanh(),
        )

    def forward(self, x):
        x = self.conv_in(x)
        x = self.res_blocks(x)
        policy = self.policy_head(x)
        value = self.value_head(x)
        return policy, value


# ── MCTS ─────────────────────────────────────────────────────────────────────


class MCTSNode:
    __slots__ = ["parent", "action", "prior", "visit_count", "value_sum", "children"]

    def __init__(self, parent=None, action=None, prior=0.0):
        self.parent = parent
        self.action = action
        self.prior = prior
        self.visit_count = 0
        self.value_sum = 0.0
        self.children = []

    def q_value(self):
        if self.visit_count == 0:
            return 0.0
        return self.value_sum / self.visit_count

    def ucb_score(self, parent_visits):
        exploration = C_PUCT * self.prior * math.sqrt(parent_visits) / (1 + self.visit_count)
        return self.q_value() + exploration

    def is_leaf(self):
        return len(self.children) == 0


def mcts_search(game, net, device, num_simulations=MCTS_SIMULATIONS, add_noise=True):
    """Run MCTS from current game state. Returns action probabilities."""
    root = MCTSNode()

    # Evaluate root
    state = torch.tensor(game.encode(), dtype=torch.float32).unsqueeze(0).to(device)
    with torch.no_grad():
        policy_logits, value = net(state)
    policy = F.softmax(policy_logits, dim=1).squeeze(0).cpu().numpy()
    mask = game.legal_moves_mask()
    policy *= mask
    policy_sum = policy.sum()
    if policy_sum > 0:
        policy /= policy_sum

    # Add Dirichlet noise at root for exploration
    if add_noise:
        noise = np.random.dirichlet([DIRICHLET_ALPHA] * ACTION_SIZE)
        policy = (1 - DIRICHLET_EPSILON) * policy + DIRICHLET_EPSILON * noise
        policy *= mask
        policy_sum = policy.sum()
        if policy_sum > 0:
            policy /= policy_sum

    # Expand root
    for action in range(ACTION_SIZE):
        if mask[action] > 0:
            root.children.append(MCTSNode(parent=root, action=action, prior=policy[action]))

    # Simulations
    for _ in range(num_simulations):
        node = root
        sim_game = game.copy()

        # Select
        while not node.is_leaf() and not sim_game.done:
            best_child = max(node.children, key=lambda c: c.ucb_score(node.visit_count))
            r, c = sim_game.action_to_coord(best_child.action)
            sim_game.play(r, c)
            node = best_child

        # Evaluate & expand
        if sim_game.done:
            if sim_game.winner is None:
                leaf_value = 0.0
            elif sim_game.winner == game.current_player:
                leaf_value = 1.0
            else:
                leaf_value = -1.0
        else:
            s = torch.tensor(sim_game.encode(), dtype=torch.float32).unsqueeze(0).to(device)
            with torch.no_grad():
                p_logits, v = net(s)
            p = F.softmax(p_logits, dim=1).squeeze(0).cpu().numpy()
            m = sim_game.legal_moves_mask()
            p *= m
            ps = p.sum()
            if ps > 0:
                p /= ps

            for action in range(ACTION_SIZE):
                if m[action] > 0:
                    node.children.append(MCTSNode(parent=node, action=action, prior=p[action]))

            # Value from current player's perspective in sim_game
            leaf_value = v.item()
            # Flip if sim_game's current player differs from root's
            if sim_game.current_player != game.current_player:
                leaf_value = -leaf_value

        # Backpropagate
        while node is not None:
            node.visit_count += 1
            # Value is from root player's perspective
            node.value_sum += leaf_value
            node = node.parent
            leaf_value = -leaf_value  # flip for parent (opponent's perspective)

    # Extract action probabilities from visit counts
    move_number = len(game.history)
    visits = np.zeros(ACTION_SIZE, dtype=np.float32)
    for child in root.children:
        visits[child.action] = child.visit_count

    if move_number < TEMPERATURE_DROP_MOVE:
        # Proportional to visit count
        total = visits.sum()
        if total > 0:
            probs = visits / total
        else:
            probs = np.ones(ACTION_SIZE) / ACTION_SIZE
    else:
        # Deterministic: pick most visited
        probs = np.zeros(ACTION_SIZE, dtype=np.float32)
        probs[np.argmax(visits)] = 1.0

    return probs


# ── Self-Play ────────────────────────────────────────────────────────────────


def self_play_game(net, device):
    """Play one game of self-play. Returns list of (state, policy, value) tuples."""
    game = GomokuGame()
    trajectory = []

    while not game.done:
        state = game.encode()
        probs = mcts_search(game, net, device, add_noise=True)

        trajectory.append((state, probs, game.current_player))

        # Sample action
        action = np.random.choice(ACTION_SIZE, p=probs)
        r, c = game.action_to_coord(action)
        game.play(r, c)

    # Assign values based on game outcome
    examples = []
    for state, probs, player in trajectory:
        if game.winner is None:
            value = 0.0
        elif game.winner == player:
            value = 1.0
        else:
            value = -1.0
        examples.append((state, probs, value))

    return examples


# ── Training ─────────────────────────────────────────────────────────────────


def train(time_budget_sec=300):
    """Train the AlphaZero agent within the given time budget.

    Returns the trained model and device.
    """
    import time

    device = get_device()
    print(f"training_device: {device}")
    net = GomokuNet().to(device)

    # Load existing model if available
    if os.path.exists(MODEL_PATH):
        net.load_state_dict(torch.load(MODEL_PATH, map_location=device, weights_only=True))

    optimizer = optim.Adam(
        net.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY
    )

    replay_buffer = []
    start_time = time.time()

    iteration = 0
    while time.time() - start_time < time_budget_sec * 0.85:
        iteration += 1
        elapsed = time.time() - start_time

        # Self-play phase
        net.eval()
        games_this_iter = max(2, SELF_PLAY_GAMES // 5)
        for g in range(games_this_iter):
            if time.time() - start_time > time_budget_sec * 0.85:
                break
            examples = self_play_game(net, device)
            replay_buffer.extend(examples)

        # Trim replay buffer
        if len(replay_buffer) > REPLAY_BUFFER_SIZE:
            replay_buffer = replay_buffer[-REPLAY_BUFFER_SIZE:]

        if len(replay_buffer) < BATCH_SIZE:
            continue

        # Training phase
        net.train()
        indices = np.arange(len(replay_buffer))
        for epoch in range(TRAIN_EPOCHS):
            if time.time() - start_time > time_budget_sec * 0.85:
                break
            np.random.shuffle(indices)
            for batch_start in range(0, len(indices) - BATCH_SIZE + 1, BATCH_SIZE):
                batch_idx = indices[batch_start : batch_start + BATCH_SIZE]

                states = torch.tensor(
                    np.array([replay_buffer[i][0] for i in batch_idx]),
                    dtype=torch.float32,
                ).to(device)
                target_policies = torch.tensor(
                    np.array([replay_buffer[i][1] for i in batch_idx]),
                    dtype=torch.float32,
                ).to(device)
                target_values = torch.tensor(
                    np.array([replay_buffer[i][2] for i in batch_idx]),
                    dtype=torch.float32,
                ).unsqueeze(1).to(device)

                policy_logits, pred_values = net(states)
                log_probs = F.log_softmax(policy_logits, dim=1)
                policy_loss = -(target_policies * log_probs).sum(dim=1).mean()
                value_loss = F.mse_loss(pred_values, target_values)
                loss = policy_loss + value_loss

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

        print(f"iter {iteration}: buffer={len(replay_buffer)}, elapsed={elapsed:.0f}s")

    # Save model
    torch.save(net.state_dict(), MODEL_PATH)
    print(f"training_complete: {iteration} iterations, {len(replay_buffer)} examples")

    return net, device


# ── Play Interface ───────────────────────────────────────────────────────────


def choose_move(game, net, device):
    """Choose a move using MCTS. Used by the evaluation harness."""
    net.eval()
    probs = mcts_search(game, net, device, num_simulations=MCTS_SIMULATIONS, add_noise=False)
    action = np.argmax(probs)
    return game.action_to_coord(action)


if __name__ == "__main__":
    train()
