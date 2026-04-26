"""Argv / env redaction for CLI subscription adapters — M3 PR 16.

Reviewer round 1 Q7 concrete spec: argv tokens matching `--api-key`,
`--token`, `--password`, `--secret` (and `=value` forms) are replaced
with `<redacted>`. Env vars whose names match
`(?i)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)` have their values stripped
from any `cli_argv` representation written to the ledger.

Test fixture MUST include `--password=hunter2` and `OPENAI_API_KEY=sk-...`
to assert neither lands in the recorded `cli_argv` (per reviewer pin).
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

# Argv flag names that carry secrets. Both standalone form
# (`--password hunter2`) and `=` form (`--password=hunter2`) covered.
_SECRET_FLAG_NAMES = frozenset({
    "--api-key",
    "--apikey",
    "--token",
    "--password",
    "--secret",
    "-p",  # common short form for --password (heuristic)
})

# Env var name pattern. Case-insensitive substring match.
_SECRET_ENV_PATTERN = re.compile(
    r"(?i)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|BEARER)"
)

REDACTED = "<redacted>"


def redact_argv(argv: Sequence[str]) -> list[str]:
    """Return a copy of argv with secret-bearing tokens redacted.

    Examples:
        ['claude', '--api-key', 'sk-...']  -> ['claude', '--api-key', '<redacted>']
        ['claude', '--password=hunter2']   -> ['claude', '--password=<redacted>']
        ['claude', '-p', 'foo']            -> ['claude', '-p', '<redacted>']
    """
    out: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            out.append(REDACTED)
            skip_next = False
            continue

        if "=" in token:
            head, _, _value = token.partition("=")
            if head in _SECRET_FLAG_NAMES:
                out.append(f"{head}={REDACTED}")
                continue

        if token in _SECRET_FLAG_NAMES:
            out.append(token)
            skip_next = True
            continue

        out.append(token)
    return out


def redact_env(env: dict[str, str]) -> dict[str, str]:
    """Return a dict with secret-named env vars' values redacted.

    Used when recording the env_allowlist exposed to the subprocess —
    we want to know WHICH env was passed (audit trail) but not the
    secret values themselves.
    """
    return {
        name: (REDACTED if _SECRET_ENV_PATTERN.search(name) else value)
        for name, value in env.items()
    }


def is_secret_env_name(name: str) -> bool:
    """Return True iff the env var name suggests a secret value."""
    return bool(_SECRET_ENV_PATTERN.search(name))
