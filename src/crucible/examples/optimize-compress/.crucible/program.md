# Text Compression Optimization

You are building a lossless text compressor from scratch.

## Goal

Maximize `compression_ratio` — defined as `original_bytes / compressed_bytes`.
Higher ratio = better compression. The corpus is ~10KB of English text.

## Hard Rules

- **DO NOT attempt to run or execute any scripts** — the platform runs them automatically
- `compress(data: bytes) -> bytes` and `decompress(compressed: bytes) -> bytes` must be defined
- `decompress(compress(data))` MUST return the original data exactly (lossless)
- **No external compression libraries**: zlib, gzip, bz2, lzma, zipfile, tarfile are FORBIDDEN (evaluate.py uses AST analysis to detect forbidden imports)
- Only Python stdlib is allowed (math, collections, struct, itertools, heapq, bisect, etc.)
- compress() must complete within 30 seconds

## Workflow

1. Read the current `compress.py` and `evaluate.py` to understand the setup
2. Think about what compression approach to try or improve
3. Edit `compress.py` with your changes
4. Explain what you changed and why you expect it to improve the ratio
