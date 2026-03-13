"""Evaluation harness for hasher.py — DO NOT MODIFY.

Hashes 50,000 keys into a table of size 65,537 (prime).
Measures uniformity using chi-square statistic.
Higher uniformity_score = more uniform distribution = better hash.

Output format (parsed by crucible):
    uniformity_score: <float>   (0.0 to 1.0, higher is better)
    collision_rate: <float>
    chi_square: <float>
"""

import ast
import random
import sys
import traceback

TABLE_SIZE = 65537  # prime
NUM_KEYS = 50_000
SEED = 12345


def check_forbidden(source: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return f"SyntaxError: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "hash":
                return "builtin hash() is forbidden — implement your own hash function"
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "hashlib":
                    return "hashlib is forbidden — implement your own hash function"
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "hashlib":
                return "hashlib is forbidden"
    return None


def generate_keys(rng: random.Random) -> list[str]:
    keys = []
    words = [
        "the", "of", "and", "a", "to", "in", "is", "you", "that", "it",
        "he", "was", "for", "on", "are", "as", "with", "his", "they", "at",
        "be", "this", "have", "from", "or", "one", "had", "by", "but", "not",
        "what", "all", "were", "we", "when", "your", "can", "said", "there",
        "use", "an", "each", "which", "she", "do", "how", "their", "if", "will",
    ]
    for _ in range(20_000):
        keys.append(rng.choice(words) + str(rng.randint(1, 999_999)))
    for _ in range(15_000):
        keys.append(
            f"{rng.randint(0, 0xffffffff):08x}-"
            f"{rng.randint(0, 0xffff):04x}-"
            f"{rng.randint(0, 0xffff):04x}"
        )
    for _ in range(15_000):
        keys.append(str(rng.randint(0, 10_000_000)))
    return keys


def main():
    with open("hasher.py", "r") as f:
        source = f.read()

    violation = check_forbidden(source)
    if violation:
        print(f"VIOLATION: {violation}")
        print("uniformity_score: 0.0")
        return

    try:
        from hasher import hash_fn

        rng = random.Random(SEED)
        keys = generate_keys(rng)

        buckets = [0] * TABLE_SIZE
        for key in keys:
            idx = hash_fn(key, TABLE_SIZE)
            if not isinstance(idx, int):
                print(f"ERROR: hash_fn must return int, got {type(idx)}")
                print("uniformity_score: 0.0")
                return
            buckets[idx % TABLE_SIZE] += 1

        collisions = sum(max(0, b - 1) for b in buckets)
        collision_rate = collisions / NUM_KEYS

        expected = NUM_KEYS / TABLE_SIZE
        chi_sq = sum((b - expected) ** 2 / expected for b in buckets)

        expected_chi = TABLE_SIZE - 1
        excess = max(0.0, chi_sq - expected_chi)
        uniformity_score = 1.0 / (1.0 + excess / TABLE_SIZE)

        print(f"uniformity_score: {uniformity_score:.4f}")
        print(f"collision_rate: {collision_rate:.4f}")
        print(f"chi_square: {chi_sq:.2f}")
        print(f"expected_chi: {expected_chi:.2f}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("uniformity_score: 0.0")


if __name__ == "__main__":
    main()
