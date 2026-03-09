"""Text compressor — this is the file the agent optimizes.

Goal: minimize the compressed size of corpus.txt.

Rules:
- compress(data) takes bytes, returns bytes (the compressed form)
- decompress(compressed) takes bytes, returns the original bytes exactly
- No external libraries (no zlib, gzip, lzma, bz2, etc.)
- Only Python stdlib math/collections/struct are allowed
- decompress(compress(data)) MUST equal data exactly (lossless)

Baseline: simple run-length encoding (RLE).
"""


def compress(data: bytes) -> bytes:
    """Compress data using run-length encoding.

    Format: for each run of identical bytes,
    emit (count, byte) where count is 1 byte (max 255).
    """
    if not data:
        return b""

    result = bytearray()
    i = 0
    while i < len(data):
        byte = data[i]
        count = 1
        while i + count < len(data) and data[i + count] == byte and count < 255:
            count += 1
        result.append(count)
        result.append(byte)
        i += count

    return bytes(result)


def decompress(compressed: bytes) -> bytes:
    """Decompress RLE-encoded data."""
    if not compressed:
        return b""

    result = bytearray()
    i = 0
    while i < len(compressed) - 1:
        count = compressed[i]
        byte = compressed[i + 1]
        result.extend([byte] * count)
        i += 2

    return bytes(result)
