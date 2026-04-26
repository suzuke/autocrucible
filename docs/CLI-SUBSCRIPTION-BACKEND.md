# SubscriptionCLIBackend (EXPERIMENTAL)

The `cli-subscription` agent backend wraps subscription-based agent CLIs — Claude Code, Codex CLI, Gemini CLI — and uses them as the LLM driver for Crucible's experiment loop.

This is an **EXPERIMENTAL** backend, gated behind multiple opt-in flags. Read this entire document before enabling it.

## What it is, what it isn't

Per spec §3.3, the wrapped CLI is a **complete agent product**. It has its own internal agent loop, its own safety policies, and unrestricted access to the host filesystem. Crucible's `CheatResistancePolicy` (the L1 ACL that constrains the `claude-code` and `smolagents` backends) does **NOT** apply to the CLI. We do not have a tool boundary to insert ACL checks on; the CLI handles its own tool calls.

What the SubscriptionCLIBackend gives you:

- **Reproducibility via scratch dir** — only declared `editable` + `readonly` files are copied into the dir the CLI is invoked in. The CLI sees a constrained workspace view; whatever it modifies inside the scratch is diff-able.
- **Truth-in-labeling** — every AttemptNode is tagged `isolation="cli_subscription_unsandboxed"` (parallel to spec §11.2 Q5's `isolation=local_unsafe`). The reporter surfaces a prominent warning banner. Audit trail is preserved.
- **Compliance gate enforcement** — adapter construction refuses unless a recent ≥99% benign-parse compliance report exists for the configured `cli_version` (spec §3.2). Override is a separate opt-in flag (`experimental.allow_stale_compliance`) with a red-letter warning.
- **Secret redaction** — argv tokens matching `--api-key|--token|--password|--secret` are redacted before being recorded in `cli_argv`. Env var values are never recorded; names are tagged `:<secret-name>` in the audit trail when secret-named.

What it **does not** give you:

- It does **not** prevent the CLI from reading host files outside the scratch dir.
- It does **not** prevent the CLI from making network requests.
- It does **not** apply Crucible's ACL.
- It does **not** make the run "isolated" or "sandboxed" or "secure" in the way the default backend (`claude-code`) is.

For stronger filesystem isolation, use the Docker sandbox configuration (`sandbox.backend: "docker"`, see `crucible/sandbox.py` for the enforced configuration per spec §INV-2). Wrapping CLI subscription mode in Docker is a valid future direction; it isn't shipped today.

## Compliance gate (spec §3.2)

Before any subscription CLI adapter ships in non-experimental mode, it must pass a benign-parse compliance gate:

- **20 standard benign tool tasks** (e.g., "read solution.py and return its contents", "list .py files in workspace").
- **Required pass rate: ≥95%** for POC admit; **≥99%** for M3 release.
- Each failed trial is classified as `parse_failure | model_refusal | format_drift | cli_error`.
- Adapters falling below the threshold are documented as "demo-only, not for production runs".

The compliance gate machinery lives at `crucible.agents.cli_subscription.compliance` (`BenignTask`, `ComplianceReport`, `verify_recent_pass()`, etc.). Reports persist to `compliance-reports/<adapter>-<datetime>.jsonl`. A run admits if the most recent report for the same `cli_binary_path` + `cli_version` is ≤30 days old AND meets the threshold.

The compliance harness CLI command (`crucible compliance-check`) is **not yet implemented** — landing in PR 16a.

## Trial result classification

Per spec §3.3, every trial result for a subscription CLI run includes a separate `provider_safety_filter_active` tag (tri-state: `detected` / `not_detected` / `unknown`). This disambiguates "the LLM provider's policy stopped the request" from "Crucible's ACL stopped the request" — without it, containment claims become unfalsifiable.

The detector hierarchy (`crucible.agents.cli_subscription.safety`):

1. **Primary**: structured stream-json events (`tool_use_denied`, `safety_classification`, `stop_reason: refusal`).
2. **Secondary**: per-adapter phrase heuristics on stdout text.
3. **UNKNOWN**: when neither primary nor secondary signals fire — explicitly the third state, never coerced to true or false.

## Configuration

```yaml
agent:
  type: cli-subscription
  cli_subscription:
    adapter: claude-code-cli      # claude-code-cli | codex-cli | gemini-cli (16b/c)
    cli_binary_path: null         # null = PATH lookup; or absolute path
    timeout_seconds: 600          # per-call timeout
    stdout_cap_bytes: 10485760    # 10 MB; subprocess killed (not just buffer-truncated) on overflow
  experimental:
    # Two-flag opt-in (both required):
    allow_cli_subscription: true        # acknowledges EXPERIMENTAL status
    acknowledge_unsandboxed_cli: true   # acknowledges ACL does NOT apply
    allow_stale_compliance: false       # require recent ≥99% gate report; flip on override
```

## What's recorded per attempt

Every AttemptNode for a subscription CLI run records (spec §4.1):

- `backend_kind`: `"cli_subscription"` (snake_case)
- `cli_binary_path`: absolute path of the binary that was invoked
- `cli_version`: snapshot of `<binary> --version` at adapter construct time
- `cli_argv`: the argv passed to subprocess (with secrets redacted via `_SECRET_FLAG_NAMES`)
- `env_allowlist`: NAMES (not values) of env vars visible to the subprocess; secret-named entries tagged `"NAME:<secret-name>"`
- `isolation`: `"cli_subscription_unsandboxed"`
- `compliance_report_path`: workspace-relative path to the JSONL evidence file that admitted this run (audit trail; null when `allow_stale_compliance` was used)

The reporter renders banner warnings when `isolation` is set, and a stronger "compliance gate bypassed" banner when `compliance_report_path` is null on a `cli_subscription_unsandboxed` run.

## Status by adapter (M3 PR 16)

| Adapter | Status |
|---|---|
| `claude-code-cli` | Active impl. Uses `claude --print --output-format=stream-json --verbose`. Wraps stream-json events. Subject to a future compliance gate run. |
| `codex-cli` | **STUB** gated to PR 16b. Calls `build_argv` / `parse_output` raise `AdapterNotImplementedError`. Factory dispatch is exhaustive from day 1 (forces base class to handle 3-adapter shape). |
| `gemini-cli` | **STUB** gated to PR 16c. Same pattern as codex-cli. |

## Risk acknowledgement

By enabling this backend, you accept that:

- The CLI runs as your user with your full filesystem and network access.
- Crucible's `CheatResistancePolicy` ACL does NOT apply to the CLI.
- The agent inside the CLI may do anything that user account is permitted to do.
- Use this backend only when you understand and accept these risks.

If you want stronger constraints, run the SubscriptionCLIBackend inside a Docker container yourself (or wait for a future PR that ships a `crucible-runner-with-cli` image). For most users, the default `claude-code` (SDK) or opt-in `smolagents` backends provide a better balance of capability and ACL.
