# Parameter Golf Demo

## Objective
Minimize `val_bpb` (bits per byte) on the FineWeb validation set.
Lower is better. The baseline scores ~3.19 BPB on mini dataset.

## Context
Based on OpenAI's Parameter Golf challenge — train the best language model
that fits in 16MB. This is a demo version using a mini dataset for fast iteration.

## What You Can Change
- `train_gpt_mlx.py`: architecture, hyperparameters, training loop

## Hard Rules
- DO NOT remove the int8 quantization + zlib compression roundtrip
- DO NOT change how val_bpb is calculated
- DO NOT add new pip dependencies
- DO NOT attempt to run or execute any scripts
- KEEP the script under 1500 lines
