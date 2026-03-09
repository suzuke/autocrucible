"""Model implementation — this is the file the agent optimizes."""

import numpy as np
from evaluate import generate_data, evaluate

# Load data
X_train, y_train, X_val, y_val = generate_data()

# Baseline: simple linear regression via least squares
# y = X @ w + b
X_train_aug = np.column_stack([X_train, np.ones(len(X_train))])
X_val_aug = np.column_stack([X_val, np.ones(len(X_val))])

w, _, _, _ = np.linalg.lstsq(X_train_aug, y_train, rcond=None)

train_pred = X_train_aug @ w
val_pred = X_val_aug @ w

evaluate(val_pred, y_val, train_pred, y_train)
