# Neural Network Classifier Optimization

You are optimizing a neural network classifier on a synthetic multi-class dataset.

## Goal

Maximize `val_accuracy` — the classification accuracy on the held-out validation set (10,000 samples, 8 classes).

## Rules

- Edit only `classifier.py`
- Your `train_and_predict(X_train, y_train, X_val)` function must return `(val_probs, train_probs)` — both are probability matrices with shape `(N, 8)` where rows sum to 1
- The evaluation harness verifies probability validity (shape, sum-to-1, no NaN)
- Training time budget: 120 seconds max (enforced by platform timeout)
- Only use numpy — no scikit-learn, no pytorch, no tensorflow
- You may restructure the code however you like, as long as the function signature stays the same

## Data

- 50,000 samples, 20 features, 8 classes
- 40k train / 10k validation (fixed split, seed=42)
- Feature breakdown: 6 informative, 4 derived nonlinear, 10 noise
- Classes defined by concentric rings + angular sectors in feature space — **highly nonlinear boundaries**
- Slight class imbalance (~1:3 ratio between rarest and most common)
- Features are pre-standardized (zero mean, unit variance on train set)

## What You Can Try

### Architecture
- Deeper networks (2-4 hidden layers)
- Wider layers (128, 256, 512 neurons)
- Skip/residual connections
- Different activation functions (GELU, Swish/SiLU, LeakyReLU, ELU)
- Batch normalization or layer normalization
- Dropout for regularization

### Optimization
- Learning rate scheduling (warmup + cosine decay, step decay)
- Momentum (SGD with momentum, Nesterov)
- Adam or AdamW optimizer
- Gradient clipping
- Weight decay / L2 regularization

### Training Strategy
- More epochs with lower learning rate
- Larger batch sizes with scaled learning rate
- Data augmentation (Gaussian noise on inputs)
- Label smoothing
- Mixup training
- Class-weighted loss for imbalanced classes
- Early stopping based on validation loss

### Feature Engineering
- Feature selection (drop noise features 10-19)
- Polynomial features on informative subset
- Radial basis functions
- Pairwise interactions

### Initialization
- He initialization for ReLU variants
- Orthogonal initialization
- Different random seeds

## Tips

- Baseline is a 1-hidden-layer (64 units) ReLU net with vanilla SGD — around 45-55% accuracy
- The data has concentric ring structure — polar coordinate features (r, theta) from x0, x1 could help a lot
- Features 10-19 are pure noise — dropping them reduces overfitting
- With the right architecture and optimizer, 75%+ accuracy is achievable
- Watch for overfitting: if train_accuracy >> val_accuracy, add regularization
- Adam typically converges much faster than vanilla SGD for this kind of problem
- Batch norm helps stabilize deeper networks significantly
