"""Crucible v1.0 trust-moat security primitives.

POC Day 1 — minimal `CheatResistancePolicy` to validate the ACL boundary story.
This module is the SSOT for path classification; M1a wires `guardrails.py`,
agent backend hooks, and `sandbox.py` shadow-mounts to all read from here.
"""

from crucible.security.cheat_resistance_policy import (
    CheatResistancePolicy,
    Classification,
    PolicyViolation,
)

__all__ = ["CheatResistancePolicy", "Classification", "PolicyViolation"]
