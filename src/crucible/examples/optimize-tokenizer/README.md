# optimize-tokenizer

Optimize a BPE-style tokenizer to compress English text into fewer tokens.

**Requirements**: None (pure Python)

## What It Does

- Agent edits `tokenizer.py` to implement `build_merges(corpus)` and `tokenize(text, merges)`
- Learns up to 500 merge rules from a training corpus, then evaluated on a different held-out corpus
- Tokenization must be lossless: joining all tokens must reconstruct the original text exactly

## Quick Start

```bash
crucible new my-tokenizer -e optimize-tokenizer
cd my-tokenizer
crucible run --tag v1
```

## Metrics

- **Metric**: tokens_per_char (minimize) -- average tokens per character
- **Baseline**: 1.0 (no merges, one token per character)
- **Eval time**: ~2-10s (30s timeout)
