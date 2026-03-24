#!/bin/bash
# Setup script for parameter-golf demo
# Downloads training script + creates mini dataset
set -e

echo "=== Installing dependencies ==="
pip install mlx numpy sentencepiece huggingface-hub datasets tqdm 2>/dev/null

echo "=== Downloading training script ==="
if [ ! -f train_gpt_mlx.py ]; then
    curl -sL https://raw.githubusercontent.com/openai/parameter-golf/main/train_gpt_mlx.py -o train_gpt_mlx.py
fi

echo "=== Downloading data ==="
if [ ! -d data/datasets/fineweb_mini_sp1024 ]; then
    # Download full data first, then create mini version
    mkdir -p data/tokenizers data/datasets/fineweb_mini_sp1024

    # Download tokenizer
    if [ ! -f data/tokenizers/fineweb_1024_bpe.model ]; then
        python3 -c "
from huggingface_hub import hf_hub_download
import shutil
path = hf_hub_download('openai-community/parameter-golf-fineweb', 'tokenizers/fineweb_1024_bpe.model')
shutil.copy(path, 'data/tokenizers/fineweb_1024_bpe.model')
print('Tokenizer downloaded')
"
    fi

    # Download 1 shard and create mini version
    python3 -c "
from huggingface_hub import hf_hub_download
import shutil, numpy as np, os

for split, fname in [('train', 'fineweb_train_000000.bin'), ('val', 'fineweb_val_000000.bin')]:
    print(f'Downloading {fname}...')
    path = hf_hub_download('openai-community/parameter-golf-fineweb', f'sp1024/{fname}')
    # Create mini version
    header = np.fromfile(path, dtype='<i4', count=256)
    header_bytes = 256 * 4
    max_tok = 1000000 if 'train' in fname else 500000
    n = min(max_tok, int(header[2]))
    n = (n // 1024) * 1024
    tokens = np.fromfile(path, dtype='<u2', count=n, offset=header_bytes)
    header[2] = n
    dst = f'data/datasets/fineweb_mini_sp1024/{fname}'
    with open(dst, 'wb') as f:
        f.write(header.tobytes())
        f.write(tokens.tobytes())
    print(f'  {dst}: {n:,} tokens')

print('Mini dataset ready')
"
fi

chmod +x evaluate.sh
echo ""
echo "=== Setup complete ==="
echo "Run: crucible run --tag demo-v1"
