import random


def estimate() -> float:
    """Estimate ∫₀¹ x² dx using plain Monte Carlo with 1000 samples."""
    N = 1000
    total = sum(random.random() ** 2 for _ in range(N))
    return total / N
