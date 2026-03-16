# Optimize Rapier Cylinder-Cylinder Collision — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Set up a crucible experiment that optimizes parry's cylinder-cylinder narrowphase collision performance by having an agent edit Rust source files.

**Architecture:** Clone parry into a standalone project directory. Crucible manages parry's git. Agent edits 2 Rust files (~13KB). evaluate.sh runs `cargo +nightly test` (correctness gate) then `cargo +nightly bench` (performance), parses ns/iter from nightly bench harness output.

**Tech Stack:** Rust (nightly for benchmarks), parry3d, crucible CLI

**Critical discovery:** Parry uses Rust's built-in nightly `#[bench]` harness (NOT Criterion). Output format: `test query::contacts::bench_cylinder_against_cylinder ... bench:       1,234 ns/iter (+/- 56)`. Requires `cargo +nightly bench`.

---

### Task 1: Clone parry and verify build

**Steps:**

**Step 1: Create project directory and clone parry**

```bash
mkdir -p ~/Documents/Hack/crucible_projects/optimize-rapier-cylinder
cd ~/Documents/Hack/crucible_projects/optimize-rapier-cylinder
git clone https://github.com/dimforge/parry.git .
```

Note: cloning with `.` makes the project root = the parry repo itself.

**Step 2: Verify nightly Rust is available**

```bash
rustup toolchain list | grep nightly
# If missing: rustup toolchain install nightly
```

**Step 3: Full release build (one-time, populates cargo cache)**

```bash
cd ~/Documents/Hack/crucible_projects/optimize-rapier-cylinder
cargo +nightly build -p parry3d --release
```

Expected: Compiles successfully (may take 2-5 minutes first time).

**Step 4: Run the cylinder benchmark to get baseline**

```bash
cargo +nightly bench -p parry3d -- cylinder_against_cylinder
```

Expected output like:
```
test query::contacts::bench_cylinder_against_cylinder ... bench:       X,XXX ns/iter (+/- XXX)
```

Record the baseline ns/iter value.

**Step 5: Run cylinder-related tests**

```bash
cargo +nightly test -p parry3d -- cylinder
```

Expected: All cylinder tests pass.

---

### Task 2: Create evaluate.sh

**Files:**
- Create: `evaluate.sh`

**Step 1: Write evaluate.sh**

```bash
#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

# Phase 1: Correctness gate — run cylinder-related tests
echo "=== Running cylinder tests ==="
if ! cargo +nightly test -p parry3d -- cylinder 2>&1; then
    echo "TESTS FAILED — correctness broken"
    echo "ns_per_iter: 999999999"
    exit 0
fi

# Phase 2: Performance measurement
echo ""
echo "=== Running cylinder benchmark ==="
BENCH_OUTPUT=$(cargo +nightly bench -p parry3d -- cylinder_against_cylinder 2>&1)
echo "$BENCH_OUTPUT"

# Parse: "test query::contacts::bench_cylinder_against_cylinder ... bench:       1,234 ns/iter (+/- 56)"
# Extract the ns/iter number (may contain commas)
NS_PER_ITER=$(echo "$BENCH_OUTPUT" | grep "cylinder_against_cylinder" | grep "ns/iter" | \
    sed 's/.*bench:[[:space:]]*//' | sed 's/[[:space:]]*ns\/iter.*//' | tr -d ',')

if [ -z "$NS_PER_ITER" ]; then
    echo "ERROR: Could not parse benchmark result"
    echo "ns_per_iter: 999999999"
    exit 0
fi

echo ""
echo "ns_per_iter: $NS_PER_ITER"
```

**Step 2: Make it executable**

```bash
chmod +x evaluate.sh
```

**Step 3: Test evaluate.sh manually**

```bash
cd ~/Documents/Hack/crucible_projects/optimize-rapier-cylinder
bash evaluate.sh
```

Expected: Tests pass, benchmark runs, outputs `ns_per_iter: <number>`.

---

### Task 3: Create .crucible/config.yaml

**Files:**
- Create: `.crucible/config.yaml`

**Step 1: Write config.yaml**

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

---

### Task 4: Create .crucible/program.md

**Files:**
- Create: `.crucible/program.md`

**Step 1: Write program.md**

```markdown
# Optimize Cylinder-Cylinder Collision Performance in Parry

## Goal
Reduce the execution time of `bench_cylinder_against_cylinder` by optimizing
the narrowphase contact manifold generation for cylinder-cylinder pairs.

## Background
Parry uses a generic GJK + PolygonalFeatureMap (PFM) path for cylinder-cylinder
collisions. There is no specialized cylinder-cylinder algorithm. The current
pipeline:
1. GJK finds closest points between two cylinders (via SupportMap trait)
2. PolygonalFeatureMap extracts approximate polygonal features:
   - Curved surface → line segment along cylinder edge
   - Cap → square approximation of circular cap
3. PolygonalFeature::contacts() clips features to generate contact manifold

## Editable Files
- `src/shape/polygonal_feature_map.rs` — Cylinder's PFM implementation (feature extraction)
- `src/query/contact_manifolds/contact_manifolds_pfm_pfm.rs` — PFM-PFM contact manifold generator

## Reference Files (readonly, read these for context)
- `src/shape/cylinder.rs` — Cylinder shape definition + SupportMap impl
- `src/query/gjk/gjk.rs` — Core GJK algorithm
- `src/shape/polygonal_feature3d.rs` — Contact point generation from polygonal features

## Optimization Strategies to Consider
- Reduce unnecessary allocations (use SmallVec, stack arrays, avoid Vec where possible)
- Add early-exit paths for common cylinder-cylinder configurations:
  - Parallel axes (simplified 2D problem)
  - Coaxial cylinders (trivial case)
  - Large separation (skip manifold generation)
- Cache or precompute rotation/projection operations
- Simplify feature extraction when contact geometry is known
- Reduce branch misprediction in hot loops
- Use SIMD-friendly data layouts where nalgebra supports it
- Inline small hot functions with #[inline] or #[inline(always)]

## Hard Rules
1. DO NOT attempt to run or execute any scripts, cargo commands, or benchmarks
2. DO NOT modify any file outside the two editable files listed above
3. DO NOT break the public API — function signatures and trait implementations must stay the same
4. DO NOT remove or weaken correctness — the collision must still produce valid contact manifolds
5. All code must compile with stable Rust idioms (the nightly feature is only for benchmarks)
6. Read BOTH editable files completely FIRST before making any changes
7. Read the readonly reference files to understand types, traits, and interfaces
8. Make ONE focused optimization per iteration — do not combine multiple changes

## Mandatory Workflow
1. READ `src/shape/polygonal_feature_map.rs` completely
2. READ `src/query/contact_manifolds/contact_manifolds_pfm_pfm.rs` completely
3. READ `src/shape/cylinder.rs` for Cylinder type and SupportMap
4. IDENTIFY the specific bottleneck you want to address
5. MAKE one focused change
6. EXPLAIN what you changed and why it should reduce ns/iter
```

---

### Task 5: Add .gitignore entries and commit setup

**Step 1: Append crucible entries to .gitignore**

Add these lines to the existing `.gitignore`:

```
# crucible
run.log
results-*.tsv
target/
```

Note: `target/` may already be in parry's .gitignore. Check first and only add if missing.

**Step 2: Commit the crucible setup files**

```bash
cd ~/Documents/Hack/crucible_projects/optimize-rapier-cylinder
git add .crucible/config.yaml .crucible/program.md evaluate.sh .gitignore
git commit -m "feat: add crucible experiment setup for cylinder collision optimization"
```

---

### Task 6: Validate with crucible

**Step 1: Run crucible validate**

```bash
cd ~/Documents/Hack/crucible_projects/optimize-rapier-cylinder
crucible validate
```

Expected: All checks pass (Config, Instructions, Editable files, Run command, Eval/metric).

**Step 2: If validate fails, debug**

Common issues:
- **Run command timeout**: validate uses `min(timeout, 120)` — first run may need full compile. Run `bash evaluate.sh` manually first to warm the cargo cache, then re-validate.
- **Metric parse failure**: Check that evaluate.sh outputs exactly `ns_per_iter: <number>` format.
- **Missing files**: Verify the parry source paths are correct for the current version.

---

### Task 7: Dry run with crucible

**Step 1: Run crucible for 1-2 iterations as smoke test**

```bash
cd ~/Documents/Hack/crucible_projects/optimize-rapier-cylinder
crucible run --tag test-1 --max-iterations 2
```

Expected: Agent reads the Rust files, makes an optimization attempt, evaluate.sh compiles and benchmarks, metric is recorded.

**Step 2: Check results**

```bash
crucible history --tag test-1
```

Verify:
- Baseline metric was captured
- Agent made a code change (not just reading)
- Compile succeeded (metric is not 999999999)
- The metric value is reasonable (hundreds to thousands of ns)

**Step 3: If the agent fails to compile**

This is expected for early iterations. Check:
- Does the error message reach the agent? (Look at run.log)
- Is the agent able to understand Rust compiler errors?
- Consider adding common Rust error patterns to program.md if needed.

**Step 4: Clean up test run**

```bash
git checkout main  # or whatever the base branch is
git branch -D crucible/test-1  # if you want to clean up
```
