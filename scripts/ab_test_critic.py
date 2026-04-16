#!/usr/bin/env python3
"""A/B test runner for Critic-Author experiment.

Compares three configurations:
  - baseline: no changes (current behavior)
  - plan_a:   failure_analysis=true (structured output from main agent)
  - plan_b:   critic.enabled=true (separate haiku critic before each iteration)

Usage:
  python scripts/ab_test_critic.py --experiments optimize-cipher optimize-pathfind
  python scripts/ab_test_critic.py --experiments optimize-cipher --iterations 10
  python scripts/ab_test_critic.py --dry-run  # show what would run
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VARIANTS = {
    "baseline": {},
    "plan_a": {"agent": {"failure_analysis": True}},
    "plan_b": {"agent": {"critic": {"enabled": True, "model": "haiku"}}},
}

DEFAULT_EXPERIMENTS = ["optimize-cipher", "optimize-pathfind", "optimize-hash"]
DEFAULT_ITERATIONS = 8


def patch_config(config_path: Path, overrides: dict) -> None:
    """Merge overrides into an existing config.yaml."""
    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f)

    for key, value in overrides.items():
        if isinstance(value, dict) and key in config and isinstance(config[key], dict):
            config[key].update(value)
        else:
            config[key] = value

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def run_experiment(
    experiment_dir: Path,
    variant: str,
    overrides: dict,
    iterations: int,
    tag_prefix: str,
) -> dict:
    """Run a single crucible experiment and return summary."""
    # Copy experiment to temp dir
    with tempfile.TemporaryDirectory(prefix=f"ab_{variant}_") as tmp:
        work_dir = Path(tmp) / experiment_dir.name
        shutil.copytree(experiment_dir, work_dir)

        # Init git
        subprocess.run(["git", "init"], cwd=work_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "ab@test.com"],
            cwd=work_dir, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "AB Test"],
            cwd=work_dir, capture_output=True, check=True,
        )

        # Run setup if exists
        config_path = work_dir / ".crucible" / "config.yaml"
        import yaml
        with open(config_path) as f:
            config = yaml.safe_load(f)
        setup_cmd = config.get("commands", {}).get("setup")
        if setup_cmd:
            subprocess.run(setup_cmd, shell=True, cwd=work_dir, capture_output=True)

        # Apply variant overrides
        if overrides:
            patch_config(config_path, overrides)

        # Set max_iterations
        patch_config(config_path, {"constraints": {"max_iterations": iterations}})

        # Git add + commit
        subprocess.run(["git", "add", "-A"], cwd=work_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=work_dir, capture_output=True, check=True,
        )

        # Run crucible
        tag = f"{tag_prefix}-{variant}"
        result = subprocess.run(
            ["crucible", "run", "--tag", tag],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=iterations * 300,  # generous timeout
        )

        # Parse results
        results_file = work_dir / f"results-{tag}.jsonl"
        records = []
        if results_file.exists():
            for line in results_file.read_text().splitlines():
                if line.strip():
                    records.append(json.loads(line))

        return {
            "variant": variant,
            "experiment": experiment_dir.name,
            "total_iterations": len(records),
            "keeps": sum(1 for r in records if r["status"] == "keep"),
            "crashes": sum(1 for r in records if r["status"] == "crash"),
            "discards": sum(1 for r in records if r["status"] == "discard"),
            "best_metric": max(
                (r["metric_value"] for r in records if r["status"] == "keep"),
                default=None,
            ),
            "total_cost_usd": sum(
                r.get("usage", {}).get("total_cost_usd", 0) or 0
                for r in records
            ),
            "total_duration_s": sum(
                r.get("duration_seconds", 0) or 0 for r in records
            ),
            "records": records,
        }


def print_comparison(results: list[dict]) -> None:
    """Print a comparison table of A/B test results."""
    print("\n" + "=" * 80)
    print("A/B TEST RESULTS")
    print("=" * 80)

    # Group by experiment
    by_experiment: dict[str, list[dict]] = {}
    for r in results:
        by_experiment.setdefault(r["experiment"], []).append(r)

    for exp, variants in by_experiment.items():
        print(f"\n--- {exp} ---")
        print(f"{'Variant':<12} {'Best':>10} {'Keeps':>6} {'Crash':>6} {'Iters':>6} {'Cost':>8} {'Time':>8}")
        print("-" * 60)
        for v in sorted(variants, key=lambda x: x["variant"]):
            best = f"{v['best_metric']:.4f}" if v["best_metric"] is not None else "N/A"
            cost = f"${v['total_cost_usd']:.3f}"
            time_s = f"{v['total_duration_s']:.0f}s"
            print(
                f"{v['variant']:<12} {best:>10} {v['keeps']:>6} "
                f"{v['crashes']:>6} {v['total_iterations']:>6} {cost:>8} {time_s:>8}"
            )


def main():
    parser = argparse.ArgumentParser(description="A/B test: Critic-Author variants")
    parser.add_argument(
        "--experiments", nargs="+", default=DEFAULT_EXPERIMENTS,
        help="Experiment names to test",
    )
    parser.add_argument(
        "--iterations", type=int, default=DEFAULT_ITERATIONS,
        help="Max iterations per run",
    )
    parser.add_argument(
        "--variants", nargs="+", default=list(VARIANTS.keys()),
        choices=list(VARIANTS.keys()),
        help="Which variants to test",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show plan without running")
    parser.add_argument(
        "--examples-dir", type=str,
        default=str(Path(__file__).parent.parent / "src" / "crucible" / "examples"),
        help="Path to examples directory",
    )
    args = parser.parse_args()

    examples_dir = Path(args.examples_dir)

    # Validate experiments exist
    for exp in args.experiments:
        exp_dir = examples_dir / exp
        if not exp_dir.exists():
            print(f"ERROR: experiment {exp} not found at {exp_dir}", file=sys.stderr)
            sys.exit(1)

    # Plan
    runs = [
        (exp, variant)
        for exp in args.experiments
        for variant in args.variants
    ]
    print(f"A/B Test Plan: {len(runs)} runs")
    for exp, variant in runs:
        print(f"  {exp} x {variant} ({args.iterations} iterations)")

    if args.dry_run:
        print("\n(dry run — not executing)")
        return

    # Execute
    results = []
    for i, (exp, variant) in enumerate(runs):
        print(f"\n[{i+1}/{len(runs)}] Running {exp} x {variant}...")
        try:
            result = run_experiment(
                examples_dir / exp, variant, VARIANTS[variant],
                args.iterations, tag_prefix=exp,
            )
            results.append(result)
            best = result["best_metric"]
            print(f"  Done: best={best}, keeps={result['keeps']}, cost=${result['total_cost_usd']:.3f}")
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "variant": variant, "experiment": exp,
                "total_iterations": 0, "keeps": 0, "crashes": 0, "discards": 0,
                "best_metric": None, "total_cost_usd": 0, "total_duration_s": 0,
                "records": [], "error": str(e),
            })

    # Summary
    print_comparison(results)

    # Save raw results
    output_path = Path("ab_test_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nRaw results saved to {output_path}")


if __name__ == "__main__":
    main()
