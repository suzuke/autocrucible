# Design: Optimize Rapier Cylinder-Cylinder Collision Performance

**Date:** 2026-03-16
**Goal:** Demonstrate crucible optimizing a Rust physics engine module; produce real perf improvements for parry's cylinder-cylinder narrowphase.

## Context

Rapier delegates collision detection to parry. Cylinder-cylinder has no specialized algorithm — it falls through to the generic GJK + PolygonalFeatureMap (PFM) path. The cylinder cap is approximated as a square. Known bugs exist (parry#396, rapier#305). The core code is ~150 lines in two files.

## Approach: Direct Fork Optimization (Option 1)

Clone parry into the project directory. Crucible manages parry's git directly. Agent edits Rust source files; evaluate.sh runs `cargo test` (correctness) then `cargo bench` (performance).

## Project Structure

```
~/Documents/Hack/crucible_projects/optimize-rapier-cylinder/   # = parry clone
├── .crucible/
│   ├── config.yaml
│   └── program.md
├── evaluate.sh
├── src/shape/polygonal_feature_map.rs          # editable — PFM feature extraction
├── src/query/contact_manifolds/
│   └── contact_manifolds_pfm_pfm.rs            # editable — manifold generator
├── crates/parry3d/benches/query/contacts.rs    # readonly — existing benchmark
└── ...                                         # rest of parry
```

## Config

```yaml
name: "optimize-rapier-cylinder"

files:
  editable:
    - "src/shape/polygonal_feature_map.rs"
    - "src/query/contact_manifolds/contact_manifolds_pfm_pfm.rs"
  readonly:
    - "src/shape/cylinder.rs"
    - "src/query/gjk/gjk.rs"
    - "src/shape/polygonal_feature3d.rs"
    - "crates/parry3d/benches/query/contacts.rs"
    - ".crucible/program.md"
  hidden:
    - "evaluate.sh"

commands:
  run: "bash evaluate.sh 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "ns_per_iter"
  direction: "minimize"

constraints:
  timeout_seconds: 300
  max_retries: 5
  plateau_threshold: 6

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

## Metric: ns_per_iter

Source: `cargo bench -p parry3d --bench contacts -- cylinder_against_cylinder`
Criterion outputs `time: [low median high]` — we extract the median value and normalize to nanoseconds.

## evaluate.sh

Two phases:
1. **Correctness gate** — `cargo test -p parry3d -- cylinder`. Failure → `ns_per_iter: 999999999` (not a crash, so agent sees "bad direction" signal in history).
2. **Performance measurement** — `cargo bench` with criterion, parse median ns/iter.

## Correctness Protection

| Layer | Mechanism |
|---|---|
| Rust compiler | Type errors, borrow checker catch structural mistakes |
| cargo test | Existing cylinder tests catch semantic errors |
| Penalty metric | Test failure → 999999999 ns (worst possible, agent learns to avoid) |
| API lock | Function signatures in readonly files; editable files implement traits |

## Agent Instructions (program.md)

- READ-first workflow enforced
- Suggested optimization strategies: allocation reduction, early-exit for common configs, precomputation, SIMD-friendly layouts
- Hard rules: no running scripts, no API changes, no correctness regression, stable Rust only

## Risks

| Risk | Probability | Mitigation |
|---|---|---|
| Incremental compile exceeds timeout | Medium | timeout=300s, first full build done manually |
| Agent produces compile errors | High | Normal flow — crucible reverts |
| Agent breaks correctness | Medium | cargo test gate + penalty metric |
| Benchmark noise | Low | Criterion statistics + possible repeat/median |
| Agent can't understand Rust generics | Medium | Readonly reference files provide type context |

## Key Design Decisions

1. **Project root = parry clone** — avoids nested git repos, crucible manages parry's git natively
2. **Test failure = extreme bad metric** (not crash) — gives agent negative signal in history
3. **Only 2 editable files** (~13KB total) — keeps agent focused, prevents collateral damage
4. **300s timeout** — accommodates incremental compile (~30-60s) + bench (~30s) + buffer
5. **No Python wrapper** — evaluate.sh directly, simplest possible
