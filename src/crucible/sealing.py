"""Sealing primitives — M2 PR 12.

M1's `eval-result.json` carried a `seal: "content-sha256:<hex>"` field that
detected accidental corruption but offered no tamper-evidence — anyone
with write access to the file could recompute a valid sha256 after
editing.

M2 introduces an opt-in HMAC-SHA256 algorithm. When the orchestrator
runs in Docker isolation (per spec §INV-2: `env_allowlist=[]`, non-root
user), the agent has no access to the host's `CRUCIBLE_SEAL_KEY`, so it
cannot forge a valid HMAC even if it can write to `eval-result.json`.
The host process can detect tampering on read.

Seal string format::

    <algorithm>:[<key-id>:]<hex>

  - `content-sha256:<hex>` (M1 — corruption check only)
  - `hmac-sha256:<key-id>:<hex>` (M2 — tamper-evident under isolation)

Schema-version is NOT bumped; the algorithm prefix differentiates
formats. Existing `content-sha256:` artefacts remain verifiable
indefinitely.

Reviewer round 1 design constraints (folded in here):
  - Default algorithm stays `content-sha256` (backward compat)
  - Single active key in v1; multi-key directory is a follow-up
  - Strict error if `algorithm: hmac-sha256` but no key resolvable
  - `verify_seal` is non-throwing on malformed seal text
  - `hmac.compare_digest()` for all comparisons (timing-safe)
  - Key precedence: `key_file` over `key_env_var`
  - Hex-encoded key, ≥32 decoded bytes, whitespace stripped
  - Never log the key or full seal — only `key_id` + algorithm
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crucible.config import SealConfig


logger = logging.getLogger(__name__)


# Minimum decoded key length in bytes. 32 bytes (256 bits) matches
# HMAC-SHA256's block size and is the recommended floor.
_MIN_KEY_LEN_BYTES = 32


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SealKeyError(RuntimeError):
    """Raised when the configured HMAC key cannot be resolved or is invalid.

    This is a configuration / startup error and propagates so the run
    fails fast — silent fallback to `content-sha256` would defeat the
    purpose of opting into HMAC.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_seal(canonical_bytes: bytes, *, config: "SealConfig") -> str:
    """Compute the seal string for the given canonical-JSON bytes.

    Dispatches on `config.algorithm`:
      - "content-sha256": plain sha256 of canonical bytes
      - "hmac-sha256":    HMAC-SHA256 keyed by the resolved key bytes

    Raises:
        SealKeyError: when algorithm requires a key but none is resolvable,
            or when the resolved key fails validation.
        ValueError:  when `algorithm` is not a recognised value.
    """
    algorithm = config.algorithm
    if algorithm == "content-sha256":
        digest = hashlib.sha256(canonical_bytes).hexdigest()
        return f"content-sha256:{digest}"
    if algorithm == "hmac-sha256":
        key_bytes = _load_key(config)
        digest = hmac.new(key_bytes, canonical_bytes, hashlib.sha256).hexdigest()
        return f"hmac-sha256:{config.key_id}:{digest}"
    raise ValueError(f"unknown seal algorithm: {algorithm!r}")


def verify_seal(
    canonical_bytes: bytes,
    seal: str,
    *,
    config: "SealConfig",
    allow_legacy_content: bool = False,
) -> bool:
    """Verify that `seal` matches `canonical_bytes` under the given config.

    **Strict-by-default policy enforcement** (M2 PR 12 reviewer round 2):
    when `config.algorithm == "hmac-sha256"`, `content-sha256:` seals are
    REJECTED unless the caller explicitly opts into legacy compatibility.
    Without this rule, an attacker who tampered with `eval-result.json`
    could replace the HMAC seal with a freshly-computed `content-sha256:`
    over the tampered bytes — no key needed — and verification would
    accept it. That's an algorithm-downgrade bypass.

    Args:
        canonical_bytes: bytes the seal was computed over (canonical JSON).
        seal: the seal string from the artefact.
        config: the project's seal policy.
        allow_legacy_content: when True, accept `content-sha256:` seals
            under any policy. Intended ONLY for migration / legacy-read
            tools (`crucible verify --legacy`, etc). DEFAULT FALSE — the
            normal verification path enforces the policy strictly.

    Returns False (does NOT raise) on:
      - malformed seal text (missing fields, non-hex, unknown algorithm)
      - algorithm mismatch under strict policy (legacy `content-sha256`
        seal seen under HMAC policy with `allow_legacy_content=False`)
      - hash/HMAC mismatch
      - key_id mismatch under hmac-sha256

    Raises SealKeyError ONLY when verification REQUIRES a key (a valid
    `hmac-sha256` seal whose key_id matches `config.key_id`) and the
    configured key is missing/unreadable. That's a config invariant
    violation, not an untrusted-input issue. Pure-content seals never
    raise.
    """
    if not isinstance(seal, str) or ":" not in seal:
        return False
    parts = seal.split(":")
    algorithm = parts[0]
    if algorithm == "content-sha256":
        # Strict policy: under HMAC config, downgrade to content-sha256
        # is a tamper bypass. Reject unless caller opts into legacy mode.
        if config.algorithm == "hmac-sha256" and not allow_legacy_content:
            return False
        if len(parts) != 2:
            return False
        expected_hex = parts[1]
        if not _is_hex(expected_hex):
            return False
        actual = hashlib.sha256(canonical_bytes).hexdigest()
        return hmac.compare_digest(actual, expected_hex)
    if algorithm == "hmac-sha256":
        if len(parts) != 3:
            return False
        seal_key_id = parts[1]
        expected_hex = parts[2]
        if not seal_key_id or not _is_hex(expected_hex):
            return False
        # Key id must match the configured active key. Multi-key rotation
        # via `.crucible/keys/<key-id>.hex` is a follow-up PR; this PR
        # supports a single configured key.
        if seal_key_id != config.key_id:
            return False
        try:
            key_bytes = _load_key(config)
        except SealKeyError:
            # Re-raise: caller's config claims a key, but it's missing.
            raise
        actual = hmac.new(key_bytes, canonical_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(actual, expected_hex)
    return False


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


def _load_key(config: "SealConfig") -> bytes:
    """Resolve the HMAC key bytes per config precedence.

    Precedence (locked by tests):
      1. `key_file` if set — read hex from disk
      2. `key_env_var` — read hex from environment

    Raises SealKeyError on:
      - both sources empty
      - file unreadable
      - non-hex content
      - decoded key shorter than _MIN_KEY_LEN_BYTES
    """
    raw_hex: str | None = None
    source: str = ""

    if config.key_file:
        path = Path(config.key_file)
        try:
            raw_hex = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SealKeyError(
                f"could not read seal key from key_file={path}: {exc}"
            ) from exc
        source = f"key_file={path}"
    else:
        import os
        raw_hex = os.environ.get(config.key_env_var, "").strip()
        source = f"key_env_var={config.key_env_var}"

    if not raw_hex:
        raise SealKeyError(
            f"seal.algorithm={config.algorithm!r} requires a key, but "
            f"{source} is empty or unset. Set CRUCIBLE_SEAL_KEY (hex-encoded "
            f"≥{_MIN_KEY_LEN_BYTES} bytes) or configure seal.key_file."
        )

    # Strip any internal whitespace so multi-line hex is tolerated.
    raw_hex = "".join(raw_hex.split())

    try:
        key_bytes = bytes.fromhex(raw_hex)
    except ValueError as exc:
        raise SealKeyError(
            f"seal key from {source} is not valid hex: {exc}"
        ) from exc

    if len(key_bytes) < _MIN_KEY_LEN_BYTES:
        raise SealKeyError(
            f"seal key from {source} is too short: "
            f"{len(key_bytes)} bytes < {_MIN_KEY_LEN_BYTES} required"
        )

    # Never log the key bytes; logging the source + algorithm is fine.
    logger.debug(
        "seal key resolved from %s for key_id=%s algorithm=%s",
        source, config.key_id, config.algorithm,
    )
    return key_bytes


def _is_hex(s: str) -> bool:
    """Return True iff `s` is a non-empty even-length hex string."""
    if not s or len(s) % 2 != 0:
        return False
    try:
        bytes.fromhex(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Startup validation hook
# ---------------------------------------------------------------------------


def validate_seal_config(config: "SealConfig") -> None:
    """Best-effort precheck to surface bad seal config before expensive work.

    Called once at orchestrator startup. For `hmac-sha256`, attempts to
    resolve the key — any failure raises SealKeyError immediately rather
    than failing on the first eval-result.json write. Cheap no-op for
    `content-sha256`.
    """
    if config.algorithm == "hmac-sha256":
        _load_key(config)  # raises SealKeyError on any failure
