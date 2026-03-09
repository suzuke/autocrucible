"""Generate a fixed test corpus for compression benchmarks.

Creates corpus.txt (~10KB) from deterministic pseudo-text so the benchmark
is fully reproducible without external downloads.
"""

import hashlib
import random

SEED = 42
TARGET_SIZE = 10_000  # ~10KB

# Common English words for realistic text generation
WORDS = [
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "I",
    "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
    "this", "but", "his", "by", "from", "they", "we", "say", "her", "she",
    "or", "an", "will", "my", "one", "all", "would", "there", "their", "what",
    "so", "up", "out", "if", "about", "who", "get", "which", "go", "me",
    "when", "make", "can", "like", "time", "no", "just", "him", "know", "take",
    "people", "into", "year", "your", "good", "some", "could", "them", "see",
    "other", "than", "then", "now", "look", "only", "come", "its", "over",
    "think", "also", "back", "after", "use", "two", "how", "our", "work",
    "first", "well", "way", "even", "new", "want", "because", "any", "these",
    "give", "day", "most", "us", "great", "between", "need", "large", "often",
    "system", "program", "number", "each", "order", "data", "information",
    "function", "process", "algorithm", "structure", "memory", "value",
    "string", "buffer", "array", "table", "index", "search", "result",
    "error", "file", "input", "output", "network", "server", "client",
    "request", "response", "message", "state", "event", "handler", "callback",
    "interface", "module", "package", "class", "method", "variable", "constant",
    "compression", "encoding", "decoding", "binary", "stream", "byte",
]

PUNCT = [".", ".", ".", ",", ",", "!", "?", ";", ":"]


def generate():
    rng = random.Random(SEED)
    paragraphs = []
    total = 0

    while total < TARGET_SIZE:
        # Generate a paragraph of 3-8 sentences
        sentences = []
        for _ in range(rng.randint(3, 8)):
            length = rng.randint(5, 20)
            words = [rng.choice(WORDS) for _ in range(length)]
            words[0] = words[0].capitalize()
            sent = " ".join(words) + rng.choice(PUNCT)
            sentences.append(sent)
        para = " ".join(sentences)
        paragraphs.append(para)
        total += len(para) + 2  # +2 for \n\n

    text = "\n\n".join(paragraphs)
    # Trim to target size
    text = text[:TARGET_SIZE]
    return text


if __name__ == "__main__":
    text = generate()
    with open("corpus.txt", "w") as f:
        f.write(text)
    print(f"Generated corpus.txt: {len(text)} bytes")
    # Print checksum for reproducibility
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    print(f"SHA256 prefix: {h}")
