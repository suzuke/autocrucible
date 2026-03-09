# Text Compression Optimization

You are building a lossless text compressor from scratch.

## Goal

Maximize `compression_ratio` — defined as `original_bytes / compressed_bytes`.
Higher ratio = better compression. The corpus is ~10KB of English-like text.

## Hard Rules (enforced by evaluate.py — violations zero the metric)

- `compress(data: bytes) -> bytes` and `decompress(compressed: bytes) -> bytes` must be defined
- `decompress(compress(data))` MUST return the original data exactly (lossless)
- **No external compression libraries**: zlib, gzip, bz2, lzma, zipfile, tarfile are FORBIDDEN
  (evaluate.py uses AST analysis to detect forbidden imports)
- Only Python stdlib is allowed (math, collections, struct, itertools, heapq, bisect, etc.)
- compress() must complete within 30 seconds

## Soft Rules

- Keep the code readable
- Prefer algorithms that are well-understood over ad-hoc tricks

## Context

- The corpus is deterministic English-like text (~10KB) with common programming terms
- Character distribution is heavily skewed (spaces, 'e', 't', 'a' are most common)
- The text has repeated words and phrases (natural language redundancy)
- Baseline RLE achieves ratio ~0.5 (expansion, not compression — text has few runs)
- Python's zlib level 9 achieves ~2.8x on this corpus (for reference, not a target)

## What You Can Try

- Huffman coding (variable-length codes based on frequency)
- LZ77 / LZ78 (sliding window / dictionary-based)
- LZW (dictionary compression, used in GIF)
- Arithmetic coding (fractional bit encoding)
- Burrows-Wheeler Transform + Move-to-Front + entropy coder
- Hybrid approaches (e.g., LZ77 + Huffman like DEFLATE)
- Context modeling (predict next byte based on previous bytes)
- PPM (Prediction by Partial Matching)

## Tips

- RLE is terrible for text (ratio < 1.0) — almost any frequency-based approach will beat it
- Huffman alone typically gets 1.5-2.0x on English text
- LZ77 with Huffman (DEFLATE-style) can reach 2.5-3.0x
- Higher-order context models (PPM) can exceed 3.0x but are harder to implement correctly
- The key insight: English text has both character-level redundancy (letter frequencies) AND word-level redundancy (repeated words/phrases). The best compressors exploit both.
- Baseline compression_ratio: ~0.5 (RLE expands the data)
