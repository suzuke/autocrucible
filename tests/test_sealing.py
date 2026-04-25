"""Tests for `crucible.sealing` — M2 PR 12 HMAC-SHA256 seal upgrade.

Covers reviewer round 1 constraints:
  - Default `content-sha256` is byte-identical to M1 output
  - HMAC mode rejects all tampering of canonical bytes
  - `verify_seal` returns False (no raise) on malformed/untrusted seal text
  - `compute_seal` raises SealKeyError when HMAC requested without key
  - Key precedence: `key_file` > `key_env_var`
  - Hex parsing, whitespace stripping, ≥32-byte length enforced
  - `key_id` mismatch under hmac → False
  - Verify never logs key bytes (we don't actually assert on logging,
    but the impl is structured so debug logs only carry id+algorithm)
  - hmac.compare_digest used for both algorithms
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

import pytest

from crucible.config import SealConfig
from crucible.sealing import (
    SealKeyError,
    compute_seal,
    verify_seal,
    validate_seal_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_HEX_KEY_64 = "a" * 64        # 32 decoded bytes → minimum allowed
_HEX_KEY_128 = "ab" * 32      # 32 decoded bytes (different content)


@pytest.fixture
def env_key(monkeypatch):
    """Install a 32-byte hex key into the default env var."""
    monkeypatch.setenv("CRUCIBLE_SEAL_KEY", _HEX_KEY_64)
    yield _HEX_KEY_64


@pytest.fixture
def file_key(tmp_path: Path) -> Path:
    p = tmp_path / "seal.key"
    p.write_text(_HEX_KEY_128)
    return p


@pytest.fixture(autouse=True)
def clear_env_seal_key(monkeypatch):
    """Each test starts with a clean CRUCIBLE_SEAL_KEY environment."""
    monkeypatch.delenv("CRUCIBLE_SEAL_KEY", raising=False)


# ---------------------------------------------------------------------------
# content-sha256 (default) — round trip + tamper detection
# ---------------------------------------------------------------------------


def test_content_sha256_roundtrip_default():
    cfg = SealConfig()  # defaults
    seal = compute_seal(b"hello world", config=cfg)
    assert seal.startswith("content-sha256:")
    assert verify_seal(b"hello world", seal, config=cfg) is True


def test_content_sha256_byte_identical_to_m1():
    """Format must be exactly `content-sha256:<hex>` (byte-identical to M1)."""
    cfg = SealConfig()
    payload = b'{"a":1,"b":"x"}'
    seal = compute_seal(payload, config=cfg)
    expected_hex = hashlib.sha256(payload).hexdigest()
    assert seal == f"content-sha256:{expected_hex}"


def test_content_sha256_tamper_detection():
    cfg = SealConfig()
    seal = compute_seal(b"original", config=cfg)
    assert verify_seal(b"tampered", seal, config=cfg) is False


def test_content_sha256_does_not_need_key():
    """No key set, no env var, no file — content-sha256 still works."""
    cfg = SealConfig()  # algorithm defaults to content-sha256
    assert compute_seal(b"x", config=cfg).startswith("content-sha256:")


# ---------------------------------------------------------------------------
# hmac-sha256 — round trip + tamper detection + key sources
# ---------------------------------------------------------------------------


def test_hmac_roundtrip_with_env_key(env_key):
    cfg = SealConfig(algorithm="hmac-sha256", key_id="default")
    seal = compute_seal(b"payload", config=cfg)
    assert seal.startswith("hmac-sha256:default:")
    assert verify_seal(b"payload", seal, config=cfg) is True


def test_hmac_tamper_detection(env_key):
    cfg = SealConfig(algorithm="hmac-sha256")
    seal = compute_seal(b"original", config=cfg)
    assert verify_seal(b"tampered", seal, config=cfg) is False


def test_hmac_format_matches_spec(env_key):
    """Format MUST be `hmac-sha256:<key-id>:<hex>`."""
    cfg = SealConfig(algorithm="hmac-sha256", key_id="rotation-1")
    seal = compute_seal(b"payload", config=cfg)
    parts = seal.split(":")
    assert parts[0] == "hmac-sha256"
    assert parts[1] == "rotation-1"
    assert len(parts[2]) == 64  # sha256 hex digest is 64 chars


def test_hmac_different_keys_produce_different_seals(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_SEAL_KEY", _HEX_KEY_64)
    cfg = SealConfig(algorithm="hmac-sha256")
    seal_1 = compute_seal(b"x", config=cfg)
    monkeypatch.setenv("CRUCIBLE_SEAL_KEY", _HEX_KEY_128)
    seal_2 = compute_seal(b"x", config=cfg)
    assert seal_1 != seal_2  # different keys → different HMACs


# ---------------------------------------------------------------------------
# Key resolution: precedence + validation
# ---------------------------------------------------------------------------


def test_key_file_precedence_over_env(file_key, monkeypatch):
    """When both key_file and CRUCIBLE_SEAL_KEY are set, key_file wins."""
    monkeypatch.setenv("CRUCIBLE_SEAL_KEY", _HEX_KEY_64)  # different key
    cfg_file = SealConfig(algorithm="hmac-sha256", key_file=str(file_key))
    cfg_env = SealConfig(algorithm="hmac-sha256")  # no key_file → env

    seal_via_file = compute_seal(b"x", config=cfg_file)
    seal_via_env = compute_seal(b"x", config=cfg_env)
    assert seal_via_file != seal_via_env  # different keys → different HMAC


def test_hmac_missing_key_raises_clear_error():
    """algorithm=hmac-sha256 with no key in env or file → SealKeyError."""
    cfg = SealConfig(algorithm="hmac-sha256")
    with pytest.raises(SealKeyError, match="requires a key"):
        compute_seal(b"x", config=cfg)


def test_hmac_unreadable_key_file_raises(tmp_path):
    cfg = SealConfig(algorithm="hmac-sha256",
                     key_file=str(tmp_path / "nonexistent.key"))
    with pytest.raises(SealKeyError, match="could not read seal key"):
        compute_seal(b"x", config=cfg)


def test_hmac_non_hex_key_rejected(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_SEAL_KEY", "not-hex-content!@#$")
    cfg = SealConfig(algorithm="hmac-sha256")
    with pytest.raises(SealKeyError, match="not valid hex"):
        compute_seal(b"x", config=cfg)


def test_hmac_short_key_rejected(monkeypatch):
    """16-byte key (< 32 minimum) is rejected."""
    monkeypatch.setenv("CRUCIBLE_SEAL_KEY", "aa" * 16)  # 16 decoded bytes
    cfg = SealConfig(algorithm="hmac-sha256")
    with pytest.raises(SealKeyError, match="too short"):
        compute_seal(b"x", config=cfg)


def test_hmac_key_whitespace_stripped(monkeypatch):
    """Multi-line / padded hex is tolerated."""
    monkeypatch.setenv("CRUCIBLE_SEAL_KEY", f"  {_HEX_KEY_64[:32]}\n  {_HEX_KEY_64[32:]}  \n")
    cfg = SealConfig(algorithm="hmac-sha256")
    seal = compute_seal(b"x", config=cfg)
    assert seal.startswith("hmac-sha256:")


def test_hmac_keyfile_with_whitespace(file_key):
    """Keyfile with trailing newline is fine (text editors add them)."""
    file_key.write_text(_HEX_KEY_128 + "\n\n")
    cfg = SealConfig(algorithm="hmac-sha256", key_file=str(file_key))
    seal = compute_seal(b"x", config=cfg)
    assert seal.startswith("hmac-sha256:")


# ---------------------------------------------------------------------------
# verify_seal: non-throwing on untrusted seal text
# ---------------------------------------------------------------------------


def test_verify_returns_false_on_malformed_seal_no_colon():
    cfg = SealConfig()
    assert verify_seal(b"x", "garbage", config=cfg) is False


def test_verify_returns_false_on_unknown_algorithm():
    cfg = SealConfig()
    assert verify_seal(b"x", "fakealgo:abcd", config=cfg) is False


def test_verify_returns_false_on_non_hex_payload():
    cfg = SealConfig()
    assert verify_seal(b"x", "content-sha256:not-hex!", config=cfg) is False


def test_verify_returns_false_on_odd_length_hex():
    cfg = SealConfig()
    assert verify_seal(b"x", "content-sha256:abcde", config=cfg) is False


def test_verify_returns_false_on_truncated_hmac_seal(env_key):
    cfg = SealConfig(algorithm="hmac-sha256")
    # Missing key_id field
    assert verify_seal(b"x", "hmac-sha256:abcdef", config=cfg) is False


def test_verify_returns_false_on_wrong_key_id(env_key):
    cfg = SealConfig(algorithm="hmac-sha256", key_id="default")
    seal = compute_seal(b"x", config=cfg)
    cfg_other = SealConfig(algorithm="hmac-sha256", key_id="other-id")
    assert verify_seal(b"x", seal, config=cfg_other) is False


def test_verify_returns_false_when_seal_key_id_blank(env_key):
    cfg = SealConfig(algorithm="hmac-sha256")
    assert verify_seal(b"x", "hmac-sha256::abcd", config=cfg) is False


# ---------------------------------------------------------------------------
# Strict-by-default policy enforcement (reviewer round 2 blocking finding)
# ---------------------------------------------------------------------------


def test_verify_rejects_content_sha256_under_hmac_policy_by_default(env_key):
    """An M1-style content-sha256 seal MUST NOT verify under an HMAC
    policy unless the caller explicitly opts into legacy mode. Otherwise
    an attacker can tamper-and-recompute a content-sha256 seal without
    any HMAC key (algorithm-downgrade bypass)."""
    m1_cfg = SealConfig(algorithm="content-sha256")
    m1_seal = compute_seal(b"payload", config=m1_cfg)

    m2_cfg = SealConfig(algorithm="hmac-sha256")
    assert verify_seal(b"payload", m1_seal, config=m2_cfg) is False


def test_verify_accepts_content_sha256_with_explicit_legacy_flag(env_key):
    """Migration tools / read-only utilities may opt into legacy compat
    by passing `allow_legacy_content=True`. This is the ONLY supported
    path to read M1 seals under an HMAC project policy."""
    m1_cfg = SealConfig(algorithm="content-sha256")
    m1_seal = compute_seal(b"payload", config=m1_cfg)

    m2_cfg = SealConfig(algorithm="hmac-sha256")
    assert verify_seal(
        b"payload", m1_seal, config=m2_cfg, allow_legacy_content=True
    ) is True


def test_legacy_flag_does_not_excuse_tampering(env_key):
    """Even with allow_legacy_content=True, a content-sha256 seal must
    only verify the unmodified bytes. The flag is a policy escape hatch,
    not a bypass of integrity checking."""
    m1_cfg = SealConfig(algorithm="content-sha256")
    m1_seal = compute_seal(b"original", config=m1_cfg)

    m2_cfg = SealConfig(algorithm="hmac-sha256")
    assert verify_seal(
        b"tampered", m1_seal, config=m2_cfg, allow_legacy_content=True
    ) is False


def test_content_policy_still_accepts_content_seals():
    """Under content-sha256 policy, content-sha256 seals continue to
    verify without any flag (M1 behavior preserved when not opting into
    HMAC)."""
    cfg = SealConfig(algorithm="content-sha256")
    seal = compute_seal(b"payload", config=cfg)
    assert verify_seal(b"payload", seal, config=cfg) is True


def test_content_policy_accepts_hmac_seal_if_key_matches(env_key):
    """An hmac-sha256 seal seen under content-sha256 policy is strictly
    stronger; if the key resolves and matches, it still verifies."""
    hmac_cfg = SealConfig(algorithm="hmac-sha256")
    hmac_seal = compute_seal(b"payload", config=hmac_cfg)

    # Under content-sha256 policy with the same key available, accept it.
    cfg_content = SealConfig(algorithm="content-sha256")
    # verify_seal still has the key resolution path; key_id="default"
    # matches our env key, so verification proceeds.
    assert verify_seal(b"payload", hmac_seal, config=cfg_content) is True


# ---------------------------------------------------------------------------
# Tampering: any field change → HMAC fails
# ---------------------------------------------------------------------------


def test_hmac_detects_any_byte_flip(env_key):
    """Even a single-byte flip should change the HMAC."""
    cfg = SealConfig(algorithm="hmac-sha256")
    base = b'{"metric_value":1.5,"valid":true}'
    seal = compute_seal(base, config=cfg)
    # Flip valid → false
    tampered = b'{"metric_value":1.5,"valid":fals}'
    assert verify_seal(tampered, seal, config=cfg) is False


def test_recomputed_content_sha256_does_not_verify_as_hmac(env_key):
    """Critical: a tamper-and-recompute attack on content-sha256 must
    not succeed when the project's policy is hmac-sha256.

    Reviewer round 2 found this exact bypass: an attacker without the
    HMAC key tampers with payload, then writes a fresh content-sha256:
    seal over the tampered bytes. The seal IS a valid content-sha256
    hash, but under HMAC policy the strict policy enforcement rejects
    the algorithm downgrade.
    """
    cfg = SealConfig(algorithm="hmac-sha256")
    real_hmac_seal = compute_seal(b"original", config=cfg)
    # Attacker tampers and recomputes a content-sha256 over tampered bytes
    fake_seal = "content-sha256:" + hashlib.sha256(b"tampered").hexdigest()
    # Under HMAC policy, the downgrade is rejected by default — this is
    # the strict-by-default behavior that closes the bypass.
    assert verify_seal(b"tampered", fake_seal, config=cfg) is False
    # The original HMAC seal does NOT verify over tampered bytes either:
    assert verify_seal(b"tampered", real_hmac_seal, config=cfg) is False


# ---------------------------------------------------------------------------
# validate_seal_config startup hook
# ---------------------------------------------------------------------------


def test_validate_seal_config_noop_for_content_sha256():
    cfg = SealConfig()
    validate_seal_config(cfg)  # no env, no file — must not raise


def test_validate_seal_config_raises_for_hmac_without_key():
    cfg = SealConfig(algorithm="hmac-sha256")
    with pytest.raises(SealKeyError):
        validate_seal_config(cfg)


def test_validate_seal_config_passes_when_key_set(env_key):
    cfg = SealConfig(algorithm="hmac-sha256")
    validate_seal_config(cfg)  # no raise


# ---------------------------------------------------------------------------
# Algorithm enum (defensive)
# ---------------------------------------------------------------------------


def test_compute_seal_unknown_algorithm_raises():
    cfg = SealConfig(algorithm="md5")
    with pytest.raises(ValueError, match="unknown seal algorithm"):
        compute_seal(b"x", config=cfg)
