#!/bin/bash
# Crucible evaluation wrapper — mini dataset for fast demo
# Each iteration: ~60s training + ~3s validation = ~65s total

METRIC_FILE="${PWD}/.crucible_metric.txt"
echo "val_bpb: ERROR" > "$METRIC_FILE"

RUN_ID=crucible_run \
DATA_PATH=./data/datasets/fineweb_mini_sp1024/ \
TOKENIZER_PATH=./data/tokenizers/fineweb_1024_bpe.model \
ITERATIONS=20000 \
TRAIN_BATCH_TOKENS=8192 \
VAL_LOSS_EVERY=0 \
VAL_BATCH_SIZE=131072 \
MAX_WALLCLOCK_SECONDS=60 \
python3 -u train_gpt_mlx.py 2>&1 | tee /dev/stderr | \
grep "final_int8_zlib_roundtrip " | tail -1 | \
sed 's/.*val_bpb:\([0-9.]*\).*/val_bpb: \1/' > "$METRIC_FILE"
