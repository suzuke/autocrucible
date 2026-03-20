# Nanoclaw Architecture Optimization — Metric Design

## Context

Using crucible to optimize the architecture/extensibility of [nanoclaw](https://github.com/qwibitai/nanoclaw), a lightweight AI assistant framework (Node.js/TypeScript) that runs Claude agents in containers.

Challenge: "good architecture" is not directly measurable. We use a combination of static analysis proxy metrics with anti-gaming guardrails.

## Design

### Crucible Config

```yaml
metric:
  name: architecture_score
  direction: maximize
commands:
  run: "npm test && tsc --noEmit"       # guardrails — fail fast, exit non-zero
  eval: "npx tsx evaluate.ts"           # outputs architecture_score: <float>
```

`commands.run` handles guardrails (tests + typecheck). If it exits non-zero, crucible marks the iteration as crashed and reverts. `commands.eval` computes and prints the metric.

### Scoring Formula

```
raw = (
    0.35 × dependency_score
  + 0.25 × size_uniformity_score
  + 0.20 × instability_score
  + 0.20 × api_surface_score
  - bad_pattern_penalties
)
score = max(0, raw)   # clamp to non-negative
```

Output: `architecture_score: <float>` on stdout.

### Sub-Metric Normalization (minimize → 0-100)

Each raw metric is "minimize = better", inverted to a 0-100 score:

| Metric | Raw value | Normalization to 0-100 |
|---|---|---|
| Dependency coupling | `avg_deps` = mean dependencies per module | `max(0, 100 - avg_deps * 20)` |
| | `circular_count` = number of circular deps | subtract `circular_count * 15` |
| File size uniformity | `gini` (range 0-1) | `(1 - gini) * 100` |
| Instability balance | `deviation` = mean abs deviation of I from 0.5 | `max(0, 100 - deviation * 200)` |
| API surface area | `avg_exports` = mean named exports per file | `max(0, 100 - avg_exports * 10)` |

Note on instability: we use a simplified target of I=0.5 per module (balanced between stable/unstable) rather than Martin's full "main sequence" which requires measuring abstractness — not cleanly applicable to TypeScript.

### Bad Pattern Penalties

| Pattern | Penalty | Rationale |
|---|---|---|
| File < 10 lines (excluding type-only files) | -3 per file | Prevents fragmentation gaming |
| Barrel re-export file (non-entry `index.ts`) | -10 per file | Prevents fake dependency reduction |
| Default export object with > 5 members | -10 per file | Prevents export consolidation gaming |
| Total file count > 2x baseline (baseline = count at initial commit) | -20 | Prevents file explosion |
| Total file count < 0.5x baseline | -20 | Prevents over-consolidation |
| Any function body > 100 lines | -5 per function | Prevents God-function gaming |

### Anti-Gaming Strategy: Three Layers

1. **Guardrails**: Tests + typecheck in `commands.run`. Can't delete functionality to improve scores.
2. **Metric tension**: Metrics constrain each other. Reducing circular deps by merging files hurts size uniformity. Flattening depth increases dependency count.
3. **Bad pattern penalties**: AST-level detection of known gaming tactics.

Core principle (Goodhart's Law): constraints must be code-enforced in evaluate, not just stated in program.md.

### File Policy

```yaml
files:
  editable:
    - "src/**/*.ts"          # core source — agent can refactor
  readonly:
    - "src/**/*.test.ts"     # tests must not be modified (guardrail integrity)
    - "evaluate.ts"
    - "package.json"
    - "tsconfig.json"
    - "vitest.config.ts"
    - "vitest.skills.config.ts"
```

**Critical**: test files (`*.test.ts`) are readonly. If editable, agent could trivially pass guardrails by weakening tests.

## Toolchain

- Dependency analysis: `madge --json --ts-config tsconfig.json src/` (must pass tsconfig for path alias resolution)
- AST analysis: `ts-morph` or regex-based
- All computation in a single `evaluate.ts` (readonly)

## Crucible Project Structure

```
.crucible/config.yaml     # metric, commands, file policies
.crucible/program.md      # agent instructions for refactoring
src/**/*.ts               # editable (excluding *.test.ts)
src/**/*.test.ts          # readonly — guardrail integrity
evaluate.ts               # readonly — computes all metrics
package.json              # readonly
tsconfig.json             # readonly
```

## Open Questions

- Penalty magnitudes (currently 3/5/10/20) need calibration on baseline nanoclaw scores
- Weight tuning may need adjustment after initial runs
- Whether `ts-morph` is worth the dependency vs simpler regex approach
- Baseline file count must be hardcoded in `evaluate.ts` after initial setup
