# FAQ

## Won't the greedy strategy get stuck in local optima?

Crucible uses a greedy keep/discard loop — improvements are kept, regressions are discarded. This sounds like it could get stuck, but an LLM agent is fundamentally different from traditional optimization:

- The agent sees **full history** including discarded and crashed attempts, so it knows what didn't work and why
- It can reason about failures and deliberately try different architectural approaches, not just parameter tweaks
- It reads the actual code each iteration, so it can make structural changes that a blind search never would

That said, local optima is a real risk for long runs. Crucible has two built-in escape hatches:

**`search.strategy: restart`** — automatically resets to the baseline commit after `plateau_threshold` stagnant iterations, injecting the full history as context so the agent tries a completely different direction:

```yaml
search:
  strategy: restart
  plateau_threshold: 8   # iters without improvement before resetting
```

**`search.strategy: beam`** — maintains `beam_width` independent branches, cycling through them in round-robin. Each branch sees a compact summary of what other branches have tried, preventing redundant exploration:

```yaml
search:
  strategy: beam
  beam_width: 3
```

**Manual multi-tag** is also available for full control:

```bash
# Explore different directions from the same baseline
crucible run --tag approach-a    # e.g. "focus on algorithmic improvements"
crucible run --tag approach-b    # e.g. "focus on low-level optimizations"
crucible compare approach-a approach-b
```

You can also backtrack to an earlier commit and branch from there:

```bash
git log crucible/run1              # find a promising commit
git checkout <commit>
crucible run --tag run1-variant    # auto-inits new branch from that point
```

## Why only one metric? What about multi-objective optimization?

The single scalar metric is a deliberate design choice that keeps the keep/discard decision unambiguous. Multi-objective trade-offs belong in your `evaluate.py`, where you have full domain knowledge to define what "better" means. See [Config Reference — Single Metric by Design](CONFIG.md#single-metric-by-design) for examples.

## Why not run multiple agents in parallel?

Crucible runs one agent per tag, serially. This is deliberate:

- **Cost efficiency**: Parallel agents multiply API costs, but serial agents learn from history — iteration N+1 is smarter than N because it sees what worked and what didn't. Blind parallel exploration doesn't have this advantage.
- **Simplicity**: Parallel agents editing the same files in the same repo cause git conflicts. Solving this requires worktree isolation, result synchronization, and merge strategies — significant complexity for marginal gain.

**The manual approach covers most needs.** Run multiple tags in separate terminals:

```bash
# Terminal 1                        # Terminal 2
crucible run --tag algo-focus       crucible run --tag lowlevel-focus
```

Each tag is an independent experiment branch. Compare results when done:

```bash
crucible compare algo-focus lowlevel-focus
```

This gives you full control over which directions to explore in parallel, with zero additional complexity.

## Is it safe to let the agent modify code that gets executed?

The agent cannot run arbitrary commands — it only has access to Read, Edit, Write, Glob, and Grep tools. However, the code it writes into editable files **is** executed by `commands.run`. If the editable file can make network requests, delete files, or perform other dangerous operations, guard rails won't catch that.

**Mitigations:**

- **Scope the editable files narrowly.** If `sort.py` only contains a sort function, the blast radius is limited even if the agent writes bad code.
- **Always set the evaluation harness as `hidden`, not `readonly`.** Readonly files are readable — the agent **will** study them and exploit implementation details (fixed seeds, scoring formulas, test data) to game the metric. In the `optimize-regression` example, the agent read `evaluate.py`, found `seed=42`, reconstructed the exact noise vector, and achieved MSE=0.0 in 3 iterations by memorizing the test set instead of learning regression. Hidden files are moved out of reach during agent execution but restored for the experiment subprocess.
- **Use `constraints.timeout_seconds`** to kill runaway experiments.
- **Use Docker sandbox** (`sandbox.backend: "docker"`) for untrusted workloads. Experiments run with network isolation, memory limits, and readonly filesystem.
- **Review the git log.** Every change is committed — you can audit exactly what the agent did.

This is the same trust model as CI/CD: you review the code, the system runs it. Crucible just automates the iteration loop.

## Where's the web dashboard?

There isn't one — by design. `results-{tag}.jsonl` is a structured JSONL file that any tool can read, and experiments typically run tens of iterations, not thousands. A full web UI would be a separate project-sized effort for marginal benefit.

**Live monitoring** (in a separate terminal):

```bash
watch -n 5 crucible status
watch -n 5 crucible history --last 10
```

**Quick trend chart:**

```bash
# Extract metrics with jq
crucible history --format jsonl | jq -r '.metric_value'

# Or Python
python3 -c "
import json, sys
for line in open('results-run1.jsonl'):
    r = json.loads(line)
    bar = '#' * int(r['metric_value'] / 10)
    print(f'{r[\"iteration\"]:3d} {r[\"metric_value\"]:8.2f} {bar}')
"
```

**Programmatic access:**

```bash
crucible status --json | jq .
crucible history --format jsonl | jq '.metric_value'
```
