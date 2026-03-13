# Tokenizer Optimization

You are optimizing a BPE-style tokenizer to compress English text.

## Goal

Minimize `tokens_per_char` — average tokens per character on a held-out test corpus.
Lower = better compression. Baseline (no merges) = 1.0 tokens/char.

## How It Works

1. `build_merges(corpus)` — analyzes `corpus.txt` and returns merge rules
2. `tokenize(text, merges)` — applies rules to tokenize text into subword tokens
3. Evaluation runs your tokenizer on a **different** English corpus (not corpus.txt)

## Rules

- Edit only `tokenizer.py`
- Return at most 500 merge rules from `build_merges()`
- Each merge rule is a `tuple[str, str]`, e.g. `("t", "h")` merges adjacent "t"+"h" → "th"
- `tokenize()` must be lossless: `"".join(tokenize(text, merges)) == text`
- Standard library only (no external packages)

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `tokenizer.py`

## Strategy

Read `corpus.txt` to understand the text style. Common BPE approach:
1. Count all adjacent character pairs in corpus
2. Merge the most frequent pair
3. Repeat until you have 500 rules

The more frequent an adjacent pair, the more tokens you save by merging it.
