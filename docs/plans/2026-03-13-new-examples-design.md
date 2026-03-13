# New Examples Design

Date: 2026-03-13

## Overview

Add 9 new example experiments to showcase Crucible's versatility across
different optimization domains: tokenization, prompt engineering, pattern
matching, hashing, quantization, reinforcement learning, and meta-programming.

## Goals

- Demo/showcase to new users
- Cover diverse problem types (algorithm, ML, AI, engineering)
- Mix of fast (< 30s) and longer-running examples
- All examples should be self-contained (minimal/zero external dependencies)

---

## Examples

### 1. `optimize-tokenizer`

**Concept**: Design BPE-style merge rules to minimize tokens per character on an English corpus.

| Item | Detail |
|------|--------|
| Editable | `tokenizer.py` — `build_merges(corpus: str) -> list[tuple[str,str]]` + `tokenize(text: str, merges: list) -> list[str]` |
| Hidden | `evaluate.py` — tests on held-out English corpus (~5KB) |
| Readonly | none |
| Metric | `tokens_per_char` (minimize) |
| Constraints | max 500 merge rules; no hardcoding test-set substrings; stdlib only |
| Timeout | 30s |
| Runtime | ~5–15s |

**Design notes**:
- Include a `corpus.txt` (training data, visible to agent) and keep test corpus inside evaluate.py
- Starter implementation: character-level tokenizer (no merges) as baseline
- Goodhart prevention: evaluate on different domain text than training corpus

---

### 2. `optimize-prompt-math`

**Concept**: Write a system prompt that maximizes Claude's accuracy on math word problems.

| Item | Detail |
|------|--------|
| Editable | `prompt.txt` — plain text system prompt |
| Hidden | `evaluate.py` — 10 math word problems with exact numeric answers; batch calls claude CLI |
| Readonly | `examples.txt` — 3 sample problems (different from test set) |
| Metric | `accuracy` (maximize, 0.0–1.0) |
| Constraints | prompt length ≤ 2000 chars; AST scan checks prompt.txt doesn't contain test answers |
| Timeout | 60s |
| Runtime | ~15–20s (1 batch claude CLI call) |

**Evaluation approach**:
```python
result = subprocess.run(
    ['claude', '-p', system_prompt,
     questions_block + "\nAnswer each as: Q1: <number> Q2: <number> ..."],
    capture_output=True, text=True, timeout=45
)
# parse Q1:, Q2:, ... and compare to ground truth
```

**Test problem examples** (not in examples.txt):
- Percentage, rate × time, mixture problems
- Answers are integers or simple decimals

---

### 3. `optimize-prompt-logic`

**Concept**: Write a system prompt that maximizes accuracy on logical reasoning problems.

| Item | Detail |
|------|--------|
| Editable | `prompt.txt` |
| Hidden | `evaluate.py` — 10 syllogism / if-then / set membership problems; True/False/Cannot determine answers |
| Readonly | `examples.txt` — 3 sample logic problems |
| Metric | `accuracy` (maximize) |
| Constraints | same as prompt-math |
| Timeout | 60s |
| Runtime | ~15–20s |

**Test problem examples**:
- "All A are B. Some B are C. Can we conclude some A are C?" → Cannot determine
- Modus ponens / modus tollens variations

---

### 4. `optimize-prompt-format`

**Concept**: Write a system prompt that makes Claude convert inputs into exact output formats.

| Item | Detail |
|------|--------|
| Editable | `prompt.txt` |
| Hidden | `evaluate.py` — 10 format conversion tasks; exact string match scoring |
| Readonly | `examples.txt` — 3 sample conversions |
| Metric | `accuracy` (maximize) |
| Constraints | same as prompt-math |
| Timeout | 60s |
| Runtime | ~15–20s |

**Test task examples**:
- Date format: "March 13, 2026" → "2026-03-13"
- Unit conversion: "5 feet 11 inches" → "180.3 cm"
- Phone normalization: "(02) 1234-5678" → "+886-2-1234-5678"

---

### 5. `optimize-regex`

**Concept**: Design a single regex pattern that maximizes F1 on a positive/negative sample set.

| Item | Detail |
|------|--------|
| Editable | `pattern.py` — `PATTERN: str` |
| Hidden | `evaluate.py` — 200 labeled samples (email address classification task) |
| Readonly | `examples.txt` — 20 sample positives + 20 negatives for the agent |
| Metric | `f1_score` (maximize) |
| Constraints | pattern must match/reject in < 1s total; no `re.fullmatch(r".*")` catch-all (AST check) |
| Timeout | 30s |
| Runtime | < 1s |

**Task**: email address validation — positive = valid email, negative = malformed
- Complex enough that naive `\S+@\S+` gets ~0.7 F1
- Good regex can reach ~0.95 F1

---

### 6. `optimize-hash`

**Concept**: Design a hash function that minimizes collision rate on a fixed key distribution.

| Item | Detail |
|------|--------|
| Editable | `hasher.py` — `hash_fn(key: str, table_size: int) -> int` |
| Hidden | `evaluate.py` — 50k string keys; measures collision rate + chi-square uniformity |
| Readonly | `key_sample.txt` — 100 sample keys so agent understands distribution |
| Metric | `uniformity_score` = 1 − collision_rate (maximize) |
| Constraints | cannot use `hash(key)` builtin or `hashlib` (AST check); pure Python arithmetic only |
| Timeout | 30s |
| Runtime | ~5s |

**Key distribution**: mix of English words, UUIDs, short numeric strings
- Forces agent to think about bit mixing and avalanche effect

---

### 7. `optimize-quantize`

**Concept**: Implement post-training quantization that maximizes accuracy-per-bit on a pre-trained classifier.

| Item | Detail |
|------|--------|
| Editable | `quantize.py` — `quantize(weights: np.ndarray, layer_name: str) -> dict` + `dequantize(q: dict) -> np.ndarray` |
| Hidden | `evaluate.py` — loads `model.npz` (pre-trained MLP), applies quantize/dequantize, runs inference on test set |
| Readonly | `model.npz` — small pre-trained NumPy MLP (~500KB, 3-layer, trained on synthetic data) |
| Metric | `score = accuracy × (32 / avg_bits_per_weight)` (maximize) |
| Constraints | no torch/bitsandbytes; pure numpy; must handle all layer shapes |
| Timeout | 60s |
| Runtime | ~20s |

**Design notes**:
- Starter: no quantization (32-bit), score = accuracy × 1.0
- INT8 symmetric: score jumps to ~accuracy × 4.0 with minor accuracy drop
- INT4 / mixed-precision: trade-off becomes interesting
- Include a `model_info.txt` describing layer shapes/sizes

---

### 8. `optimize-rl-policy`

**Concept**: Design a policy for CartPole that maximizes mean episode steps (self-contained physics, no gym).

| Item | Detail |
|------|--------|
| Editable | `policy.py` — `select_action(obs: list[float]) -> int` where obs = [pos, vel, angle, angular_vel] |
| Hidden | `evaluate.py` — CartPole physics (self-contained ~80 lines); runs 200 episodes |
| Readonly | `obs_info.txt` — description of observation space and action meanings |
| Metric | `mean_steps` (maximize, theoretical max 500) |
| Constraints | no gym/torch; pure Python + stdlib; policy must be deterministic (no random calls allowed) |
| Timeout | 60s |
| Runtime | ~15s |

**Design notes**:
- Starter: random policy → ~20 mean steps
- Simple angle-based heuristic: ~150 steps
- Linear policy / LQR: ~400–500 steps
- CartPole physics implemented from scratch (no dependency)

---

### 9. `optimize-codegen`

**Concept**: Write a code generator that, given task specs, produces Python code that runs correctly and fast.

| Item | Detail |
|------|--------|
| Editable | `generator.py` — `generate(spec: dict) -> str` (returns Python code string) |
| Hidden | `evaluate.py` — 10 task specs; exec()s generated code in sandbox; measures correctness + speed |
| Readonly | `spec_schema.txt` — describes the spec format + 2 example specs |
| Metric | `score = mean(correctness × speed_ratio)` (maximize) |
| Constraints | generated code cannot import ctypes/subprocess/os.system; generator must finish in 2s total |
| Timeout | 60s |
| Runtime | ~30s |

**Task spec examples** (not all visible to agent):
```python
{"task": "sum_of_squares", "n": 10000}  # sum i^2 for i in 1..n
{"task": "find_primes", "limit": 1000}   # list all primes up to limit
{"task": "matrix_trace", "size": 100}    # trace of random int matrix
```

**Speed scoring**: compare runtime to reference naive implementation
- `speed_ratio = min(reference_time / generated_time, 10.0)` (capped)

---

## File Structure Per Example

Each example follows the standard Crucible layout:

```
optimize-<name>/
  .crucible/
    config.yaml       # metric, commands, file policies
    program.md        # agent instructions
  <editable>.py       # what the agent modifies
  evaluate.py         # hidden evaluation harness
  <readonly>.txt      # optional reference data
  .gitignore
```

## Implementation Priority

1. Fast examples first (tokenizer, regex, hash) — validate approach
2. Prompt examples (math, logic, format) — need claude CLI integration testing
3. ML examples (quantize, rl-policy) — need asset generation (model.npz, physics sim)
4. Meta example (codegen) — most complex, implement last

## Goodhart Prevention Per Example

| Example | Prevention Mechanism |
|---------|---------------------|
| tokenizer | Evaluate on different domain than training corpus |
| prompt-* | Test questions hidden; prompt scanned for hardcoded answers |
| regex | Large held-out set (200 samples); agent only sees 40 |
| hash | 50k keys; `hash()` builtin banned via AST |
| quantize | Test set hidden; model.npz not editable |
| rl-policy | Physics sim hidden; no random seed exposure |
| codegen | 8/10 specs hidden; exec sandbox prevents cheating |
