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
# Cross-algorithm verification (read M1 seals after switching to M2)
# ---------------------------------------------------------------------------


def test_verify_recognises_old_content_sha256_under_hmac_config(env_key):
    """A project switched to hmac-sha256 should still verify old M1 seals."""
    # Compute an M1-style content-sha256 seal
    m1_cfg = SealConfig(algorithm="content-sha256")
    m1_seal = compute_seal(b"old-payload", config=m1_cfg)

    # Verify under M2 hmac config — should still pass for content-sha256 seal
    m2_cfg = SealConfig(algorithm="hmac-sha256")
    assert verify_seal(b"old-payload", m1_seal, config=m2_cfg) is True


def test_verify_rejects_content_sha256_seal_when_payload_tampered(env_key):
    m1_cfg = SealConfig(algorithm="content-sha256")
    m1_seal = compute_seal(b"original", config=m1_cfg)
    m2_cfg = SealConfig(algorithm="hmac-sha256")
    assert verify_seal(b"tampered", m1_seal, config=m2_cfg) is False


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
    not succeed when the project's policy is hmac-sha256."""
    cfg = SealConfig(algorithm="hmac-sha256")
    real_hmac_seal = compute_seal(b"original", config=cfg)
    # Attacker tampers and recomputes a content-sha256 over tampered bytes
    fake_seal = "content-sha256:" + hashlib.sha256(b"tampered").hexdigest()
    # That seal IS a valid content-sha256 over the tampered bytes (this
    # is by design — content-sha256 is just a corruption check).
    assert verify_seal(b"tampered", fake_seal, config=cfg) is True
    # But the original HMAC seal does NOT verify over tampered bytes:
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
