"""Tokenizer implementation — edit this file to improve compression.

Interface:
  build_merges(corpus: str) -> list[tuple[str, str]]
      Analyze corpus and return up to 500 BPE merge rules.
      Each rule is a pair of adjacent token strings to merge, e.g. ("t", "h") -> "th".

  tokenize(text: str, merges: list[tuple[str, str]]) -> list[str]
      Apply merge rules to text and return list of tokens.

Baseline: no merges → every character is its own token → tokens_per_char = 1.0
Goal: find merge rules that reduce tokens_per_char on held-out text.
"""


def build_merges(corpus: str) -> list[tuple[str, str]]:
    """Return merge rules derived from corpus. Max 500 rules."""
    # Baseline: no merges (character-level tokenization)
    return []


def tokenize(text: str, merges: list[tuple[str, str]]) -> list[str]:
    """Apply merge rules to tokenize text into subword tokens."""
    # Start: split into individual characters
    tokens = list(text)

    for a, b in merges:
        merged = a + b
        new_tokens = []
        i = 0
        while i < len(tokens):
            if i < len(tokens) - 1 and tokens[i] == a and tokens[i + 1] == b:
                new_tokens.append(merged)
                i += 2
            else:
                new_tokens.append(tokens[i])
                i += 1
        tokens = new_tokens

    return tokens
