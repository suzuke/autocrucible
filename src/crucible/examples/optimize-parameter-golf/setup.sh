#!/bin/bash
# Setup script for parameter-golf demo
# Downloads training script + creates mini dataset for fast iteration
set -e

echo "=== Installing dependencies ==="
pip install mlx numpy sentencepiece huggingface-hub datasets tqdm 2>/dev/null

echo "=== Downloading training script and data downloader ==="
if [ ! -f train_gpt_mlx.py ]; then
    curl -sL https://raw.githubusercontent.com/openai/parameter-golf/main/train_gpt_mlx.py -o train_gpt_mlx.py
    echo "Downloaded train_gpt_mlx.py"
fi
mkdir -p data
if [ ! -f data/cached_challenge_fineweb.py ]; then
    curl -sL https://raw.githubusercontent.com/openai/parameter-golf/main/data/cached_challenge_fineweb.py -o data/cached_challenge_fineweb.py
    echo "Downloaded data downloader"
fi

echo "=== Downloading FineWeb data (1 shard) ==="
if [ ! -d data/datasets/fineweb10B_sp1024 ]; then
    python3 data/cached_challenge_fineweb.py --variant sp1024 --train-shards 1
fi

echo "=== Creating mini dataset ==="
if [ ! -d data/datasets/fineweb_mini_sp1024 ]; then
    python3 -c "
import numpy as np, os

def make_mini_shard(src, dst, max_tokens):
    header = np.fromfile(src, dtype='<i4', count=256)
    n = min(max_tokens, int(header[2]))
    n = (n // 1024) * 1024
    tokens = np.fromfile(src, dtype='<u2', count=n, offset=256*4)
    header[2] = n
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, 'wb') as f:
        f.write(header.tobytes())
        f.write(tokens.tobytes())
    print(f'  {dst}: {n:,} tokens')

print('Creating mini shards...')
make_mini_shard(
    'data/datasets/fineweb10B_sp1024/fineweb_train_000000.bin',
    'data/datasets/fineweb_mini_sp1024/fineweb_train_000000.bin',
    1_000_000)
make_mini_shard(
    'data/datasets/fineweb10B_sp1024/fineweb_val_000000.bin',
    'data/datasets/fineweb_mini_sp1024/fineweb_val_000000.bin',
    500_000)
print('Mini dataset ready')
"
fi

chmod +x evaluate.sh

echo "=== Adding train script to git ==="
git add train_gpt_mlx.py 2>/dev/null && git commit -m "add train_gpt_mlx.py" 2>/dev/null || true

echo ""
echo "=== Setup complete ==="
echo "Run: crucible run --tag demo-v1"
