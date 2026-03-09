# Gomoku AlphaZero Agent Optimization

You are optimizing an AlphaZero-style Gomoku (五子棋) agent on a 9×9 board.

## Goal

Maximize `win_rate` — weighted win rate against baseline opponents (30% vs Random + 70% vs Greedy). Range: 0–100.

## Rules

- Edit only `agent.py`
- Must export: `train(time_budget_sec) -> (net, device)`, `choose_move(game, net, device) -> (row, col)`, `GomokuNet`, `MODEL_PATH`
- `train()` must complete within the given time budget (300s)
- `choose_move()` is called by the evaluation harness — must return a valid `(row, col)` tuple
- The game engine (`game.py`) and evaluation harness (`evaluate.py`) are read-only
- You may use: `torch`, `numpy`, and Python standard library

## Architecture (AlphaZero)

The baseline implements three core AlphaZero components:

1. **Neural Network** (`GomokuNet`): Dual-headed CNN with residual blocks
   - Input: 3 channels (own stones, opponent stones, color indicator)
   - Policy head: move probabilities over all board positions
   - Value head: position evaluation (-1 to +1)

2. **Monte Carlo Tree Search** (`mcts_search`): Guided by the neural network
   - Selection via PUCT (polynomial upper confidence bound for trees)
   - Expansion and evaluation using the neural network
   - Backpropagation of values through the tree

3. **Self-Play Training**: Generate training data by playing against itself
   - MCTS produces move policies, game outcomes provide value targets
   - Train on (state, policy, value) tuples from replay buffer

## What You Can Try

### Network Architecture
- Adjust `NUM_CHANNELS` (width) and `NUM_RES_BLOCKS` (depth)
- Try squeeze-and-excitation blocks, attention mechanisms
- Experiment with different input features (e.g., liberties, threat patterns)
- Try global average pooling in value head

### MCTS Parameters
- Tune `MCTS_SIMULATIONS`, `C_PUCT` (exploration constant)
- Adjust `TEMPERATURE` schedule and `TEMPERATURE_DROP_MOVE`
- Tune Dirichlet noise (`DIRICHLET_ALPHA`, `DIRICHLET_EPSILON`)
- Try virtual loss for more efficient tree exploration

### Training Strategy
- Adjust `LEARNING_RATE`, `WEIGHT_DECAY`, `BATCH_SIZE`
- Change `SELF_PLAY_GAMES` per iteration and `REPLAY_BUFFER_SIZE`
- Try learning rate scheduling (cosine annealing, warmup)
- Add data augmentation (board rotations/reflections — 8-fold symmetry)
- Try prioritized experience replay

### Gomoku-Specific Improvements
- Add threat detection in input features (open-4, half-open-4, etc.)
- Hard-code immediate win/block moves (skip MCTS when obvious)
- Use progressive widening in MCTS for early game
- Add pattern-based heuristics to initialize policy prior

## Tips

- The 300s training budget is tight — balance self-play games vs training epochs
- On MPS/CPU, fewer MCTS simulations per move is practical (~50-100)
- Board symmetry augmentation gives 8× more training data for free
- Greedy opponent is the harder baseline — focus on beating it
- The evaluation uses `EVAL_MCTS_SIMS=50` — `choose_move` should work well with limited search
- Baseline win rate: ~30-50% depending on hardware (more training time = better)
- Immediate tactical play (win/block) matters more than deep strategy at this level
