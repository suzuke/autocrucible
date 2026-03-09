"""Evaluation harness for text compression — DO NOT MODIFY.

Measures compression ratio on a fixed corpus.
Gates on lossless correctness: decompress(compress(data)) == data.

Output format (parsed by crucible):
    compression_ratio: <float>   (primary metric, higher = better compression)
    compressed_bytes: <int>
    original_bytes: <int>
    correct: <bool>
"""

import hashlib
import sys
import time
import traceback

# Expected corpus checksum (from generate_corpus.py with SEED=42)
EXPECTED_SHA256_PREFIX = "e26598e9ebed027c"
CORPUS_FILE = "corpus.txt"

# Forbidden modules — agent must not use built-in compression
FORBIDDEN_MODULES = frozenset([
    "zlib", "gzip", "bz2", "lzma", "zipfile", "tarfile",
    "codecs",  # has some compression codecs
])


def load_corpus():
    """Load and verify the test corpus."""
    with open(CORPUS_FILE, "r") as f:
        text = f.read()
    data = text.encode("utf-8")
    h = hashlib.sha256(data).hexdigest()[:16]
    if h != EXPECTED_SHA256_PREFIX:
        print(f"WARNING: corpus checksum mismatch: {h} != {EXPECTED_SHA256_PREFIX}")
    return data


def verify_no_forbidden_imports():
    """Check that compress.py doesn't import forbidden modules."""
    import ast
    with open("compress.py", "r") as f:
        source = f.read()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "compress.py has syntax errors"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod in FORBIDDEN_MODULES:
                    return f"Forbidden import: {mod}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split(".")[0]
                if mod in FORBIDDEN_MODULES:
                    return f"Forbidden import: from {mod}"
    return None


def main():
    # Check forbidden imports first
    violation = verify_no_forbidden_imports()
    if violation:
        print(f"VIOLATION: {violation}")
        print("compression_ratio: 0.0")
        print("correct: false")
        return

    data = load_corpus()
    original_size = len(data)
    print(f"original_bytes: {original_size}")

    try:
        from compress import compress, decompress

        # Compress
        t0 = time.perf_counter()
        compressed = compress(data)
        compress_time = time.perf_counter() - t0

        if not isinstance(compressed, (bytes, bytearray)):
            print("ERROR: compress() must return bytes")
            print("compression_ratio: 0.0")
            print("correct: false")
            return

        compressed_size = len(compressed)
        print(f"compressed_bytes: {compressed_size}")
        print(f"compress_time_ms: {compress_time * 1000:.1f}")

        # Decompress
        t0 = time.perf_counter()
        decompressed = decompress(compressed)
        decompress_time = time.perf_counter() - t0
        print(f"decompress_time_ms: {decompress_time * 1000:.1f}")

        if not isinstance(decompressed, (bytes, bytearray)):
            print("ERROR: decompress() must return bytes")
            print("compression_ratio: 0.0")
            print("correct: false")
            return

        # Correctness check: lossless round-trip
        if decompressed != data:
            # Find first difference for debugging
            for i in range(min(len(decompressed), len(data))):
                if decompressed[i] != data[i]:
                    print(f"ERROR: first mismatch at byte {i}: "
                          f"got {decompressed[i]:#x}, expected {data[i]:#x}")
                    break
            else:
                print(f"ERROR: length mismatch: got {len(decompressed)}, "
                      f"expected {len(data)}")
            print("compression_ratio: 0.0")
            print("correct: false")
            return

        # Compression ratio: original / compressed (higher = better)
        # ratio > 1.0 means actual compression
        # ratio < 1.0 means expansion (worse than no compression)
        if compressed_size == 0:
            print("ERROR: compressed size is 0")
            print("compression_ratio: 0.0")
            print("correct: false")
            return

        ratio = original_size / compressed_size
        print(f"compression_ratio: {ratio:.4f}")
        print("correct: true")

        # Reference: Python's zlib for comparison
        import zlib
        zlib_compressed = zlib.compress(data, 9)
        zlib_ratio = original_size / len(zlib_compressed)
        print(f"zlib_ratio: {zlib_ratio:.4f}")
        print(f"zlib_bytes: {len(zlib_compressed)}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("compression_ratio: 0.0")
        print("correct: false")


if __name__ == "__main__":
    main()
