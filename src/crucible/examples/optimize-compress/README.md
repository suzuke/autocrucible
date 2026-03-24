# optimize-compress

Build a lossless text compressor from scratch without using any compression libraries.

**Requirements**: None (pure Python, no zlib/gzip/bz2/lzma)

## What It Does

- Agent edits `compress.py` to implement `compress(data) -> bytes` and `decompress(compressed) -> bytes`
- Decompression must perfectly reconstruct the original data (lossless, verified by evaluator)
- Forbidden imports (zlib, gzip, bz2, lzma, etc.) are detected via AST analysis

## Quick Start

```bash
crucible new my-compress -e optimize-compress
cd my-compress
crucible run --tag v1
```

## Metrics

- **Metric**: compression_ratio (maximize) -- original_bytes / compressed_bytes
- **Baseline**: 1.0 (no compression)
- **Eval time**: ~5-15s (30s compress timeout, 60s total)
