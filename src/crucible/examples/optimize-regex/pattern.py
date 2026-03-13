"""Email pattern — edit PATTERN to improve F1 score on the held-out test set.

PATTERN is a single Python regex string.
It will be tested with re.fullmatch(PATTERN, email_address).

Baseline: simple pattern that gets ~0.70 F1.
A well-crafted pattern can reach ~0.95+ F1.
"""

# Baseline: catches most emails but misses edge cases
PATTERN = r"\S+@\S+\.\S+"
