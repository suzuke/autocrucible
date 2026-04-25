"""POC Day 3 (zero-token) — mutation-based ACL containment tests.

Per spec §5.4: from a small seed corpus of attacks, mechanically generate ~500
variants and assert `CheatResistancePolicy` catches them all. This complements
Day 1's hand-curated and Hypothesis property tests by stress-testing the same
attack categories at scale across encoding / case / glob / argument-injection
/ multi-step axes.

Categories:
  M1. Path-encoding mutations (percent-encoding, unicode normalisation, etc.)
  M2. Case mutations (uppercase/lowercase variants)
  M3. Whitespace / control-character injection in path strings
  M4. Multi-step attacks (build artefact, then attempt the actual escape)
  M5. Glob/wildcard probes (NOT classify(); manual glob simulator)
  M6. Combined seed × mutation (cartesian product)
"""

from __future__ import annotations

import itertools
import os
import unicodedata
import urllib.parse
from pathlib import Path

import pytest

from crucible.security import CheatResistancePolicy


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "solution.py").write_text("# editable")
    (tmp_path / "evaluate.py").write_text("# readonly")
    (tmp_path / ".env").write_text("SECRET=42")
    (tmp_path / "subdir").mkdir(exist_ok=True)
    (tmp_path / "subdir" / "data.txt").write_text("data")
    return tmp_path


@pytest.fixture
def policy(workspace: Path) -> CheatResistancePolicy:
    return CheatResistancePolicy.from_lists(
        workspace=workspace,
        editable=["solution.py"],
        readonly=["evaluate.py"],
        hidden=[".env"],
    )


# ---------------------------------------------------------------------------
# Seed corpus — attacks we want every mutation of to remain caught
# ---------------------------------------------------------------------------


SEED_ATTACKS = [
    # Path traversal escapes (must always classify "unlisted")
    ("../etc/passwd", "unlisted"),
    ("../../etc/passwd", "unlisted"),
    ("../../../etc/passwd", "unlisted"),
    ("./..//etc/passwd", "unlisted"),
    ("subdir/../../etc/passwd", "unlisted"),
    ("solution.py/../../../etc/passwd", "unlisted"),
    # Absolute paths outside workspace
    ("/etc/passwd", "unlisted"),
    ("/etc/shadow", "unlisted"),
    ("/tmp/anything", "unlisted"),
    ("/home/somebody/.ssh/id_rsa", "unlisted"),
    # Hidden file references (must always classify "hidden", never "editable")
    (".env", "hidden"),
    ("./.env", "hidden"),
    ("./subdir/../.env", "hidden"),
    # Readonly file references
    ("evaluate.py", "readonly"),
    ("./evaluate.py", "readonly"),
    # Direct empty / weird strings
    ("", "unlisted"),
    (".", "unlisted"),  # workspace itself
    ("..", "unlisted"),  # parent of workspace
]


# ---------------------------------------------------------------------------
# M1 — encoding mutations
# ---------------------------------------------------------------------------


def _percent_encode(s: str, double: bool = False) -> str:
    out = urllib.parse.quote(s, safe="")
    if double:
        out = urllib.parse.quote(out, safe="")
    return out


def _unicode_normalise_variants(s: str) -> list[str]:
    """Generate a few unicode-normalized variants (NFC, NFD, NFKC, NFKD)."""
    return [unicodedata.normalize(form, s) for form in ("NFC", "NFD", "NFKC", "NFKD")]


@pytest.mark.parametrize("seed, expected", SEED_ATTACKS)
def test_seed_attacks_classify_correctly(policy, workspace, seed, expected):
    """Sanity: every seed attack classifies as expected with no mutation applied."""
    cls = policy.classify(workspace / seed if seed else workspace)
    assert cls == expected, f"seed {seed!r} → {cls}, expected {expected}"


@pytest.mark.parametrize("seed, expected", SEED_ATTACKS)
def test_unicode_normalisation_variants(policy, workspace, seed, expected):
    """Unicode normalisation must not upgrade hidden/unlisted to editable.

    For each seed, try NFC/NFD/NFKC/NFKD forms. Classification may relax to
    'unlisted' if the normalised form no longer matches a known file (which
    is fine — the attack still doesn't get write access), but it must never
    upgrade to 'editable'.
    """
    for variant in _unicode_normalise_variants(seed):
        if not variant:
            continue
        cls = policy.classify(workspace / variant)
        assert cls != "editable", (
            f"unicode normalised {variant!r} (from {seed!r}) → editable (LEAK)"
        )


# ---------------------------------------------------------------------------
# M2 — case mutations
# ---------------------------------------------------------------------------


def _case_variants(s: str) -> list[str]:
    """Return a handful of case-flipped variants without affecting `/`, `.`, etc."""
    if not s:
        return [s]
    return [
        s,
        s.upper(),
        s.lower(),
        s.title(),
        s.swapcase(),
    ]


@pytest.mark.parametrize("seed, expected", SEED_ATTACKS)
def test_case_mutations_do_not_upgrade_to_editable(policy, workspace, seed, expected):
    """Case-mutated paths may stop matching anything (→ unlisted) but must
    never become editable. On case-insensitive FS (macOS APFS default) the
    upper/title variants might still match; on case-sensitive FS they won't."""
    for variant in _case_variants(seed):
        cls = policy.classify(workspace / variant if variant else workspace)
        # On case-insensitive FS, the variant resolves to the same file.
        # If seed was 'editable', variant might also be editable; but if seed
        # was hidden/readonly/unlisted, variant must NEVER become editable.
        if expected != "editable":
            assert cls != "editable", (
                f"case variant {variant!r} of {seed!r} → editable (LEAK)"
            )


# ---------------------------------------------------------------------------
# M3 — whitespace / control char injection
# ---------------------------------------------------------------------------


_INJECTION_SUFFIXES = [
    "",
    " ",
    "\t",
    "\n",
    "\r",
    "\r\n",
    "\x00",      # null byte
    "/",
    "//",
    "/.",
    "/..",
]


@pytest.mark.parametrize("seed, expected", SEED_ATTACKS)
@pytest.mark.parametrize("suffix", _INJECTION_SUFFIXES)
def test_injection_suffix_does_not_upgrade(policy, workspace, seed, expected, suffix):
    """Appending whitespace, control chars, or extra slashes to an attack must
    not upgrade hidden/readonly/unlisted to editable."""
    if not seed:
        return  # empty seed already covered
    try:
        variant = seed + suffix
        cls = policy.classify(workspace / variant)
    except (OSError, ValueError):
        # Some control chars may raise on resolve; that's a containment hit.
        return
    if expected != "editable":
        assert cls != "editable", (
            f"variant {variant!r} of {seed!r} → editable (LEAK)"
        )


# ---------------------------------------------------------------------------
# M4 — multi-step attacks (build a stepping-stone, then probe)
# ---------------------------------------------------------------------------


def test_multi_step_create_symlink_then_edit(policy, workspace):
    """Step 1: agent creates a symlink alias for hidden .env at editable position.
    Step 2: agent attempts to edit alias.

    Expected: classify(alias) → hidden; assert_writable raises.
    """
    # Step 1
    sol = workspace / "solution.py"
    sol.unlink()
    sol.symlink_to(workspace / ".env")
    # Step 2
    assert policy.classify(sol) == "hidden"
    from crucible.security import PolicyViolation
    with pytest.raises(PolicyViolation):
        policy.assert_writable(sol)


def test_multi_step_create_hardlink_then_edit_blocked(policy, workspace):
    """Hardlink alias to readonly file, attempt to edit alias."""
    sol = workspace / "solution.py"
    sol.unlink()
    os.link(workspace / "evaluate.py", sol)
    assert policy.classify(sol) == "readonly"
    from crucible.security import PolicyViolation
    with pytest.raises(PolicyViolation):
        policy.assert_writable(sol)


def test_multi_step_chained_traversal_after_subdir_create(policy, workspace):
    """Agent creates deeper subdir, then traverses through it to escape."""
    nested = workspace / "subdir" / "nested" / "deep"
    nested.mkdir(parents=True, exist_ok=True)
    attack = nested / ".." / ".." / ".." / ".env"
    cls = policy.classify(attack)
    # The attack resolves to .env which is hidden; that's correct.
    assert cls == "hidden"


def test_multi_step_create_unlisted_then_link_to_readonly(policy, workspace):
    """Agent creates an unlisted file path, then hardlinks it to evaluate.py.

    The unlisted file becomes a hardlink alias to readonly. Agent then tries
    to edit it — must be classified readonly via inode collision.
    """
    target = workspace / "decoy.py"
    os.link(workspace / "evaluate.py", target)
    cls = policy.classify(target)
    assert cls == "readonly", f"hardlinked decoy → {cls} (expected readonly)"


# ---------------------------------------------------------------------------
# M5 — combined seed × encoding × case × suffix (cartesian explosion)
# ---------------------------------------------------------------------------


def _combinatoric_variants() -> list[tuple[str, str, str]]:
    """Return list of (label, variant, expected_not_editable_for_seed_label)
    triples. Generates ~500 cases."""
    out: list[tuple[str, str, str]] = []
    case_funcs = [str, str.upper, str.lower, str.swapcase]
    suffixes = ["", " ", "/", "/.", "//"]
    encodings = [
        ("plain", lambda s: s),
        ("nfc", lambda s: unicodedata.normalize("NFC", s)),
        ("nfd", lambda s: unicodedata.normalize("NFD", s)),
        ("nfkc", lambda s: unicodedata.normalize("NFKC", s)),
        ("nfkd", lambda s: unicodedata.normalize("NFKD", s)),
    ]

    # Seeds that should NEVER classify as editable
    not_editable_seeds = [
        s for s, exp in SEED_ATTACKS if exp != "editable"
    ]

    for seed, case_fn, suffix, (enc_label, enc_fn) in itertools.product(
        not_editable_seeds, case_funcs, suffixes, encodings
    ):
        if not seed:
            continue
        variant = enc_fn(case_fn(seed)) + suffix
        out.append((f"{enc_label}|{case_fn.__name__}|{suffix!r}|{seed!r}", variant, seed))
    return out


_COMBO_VARIANTS = _combinatoric_variants()


@pytest.mark.parametrize("label, variant, seed", _COMBO_VARIANTS,
                         ids=[v[0] for v in _COMBO_VARIANTS])
def test_combinatoric_mutation_no_upgrade_to_editable(policy, workspace, label, variant, seed):
    """For all seed×encoding×case×suffix combinations of seeds that are NOT
    editable, the resulting classification must NEVER be 'editable'.

    This is the key invariant: no mutation can grant write access to a file
    that the policy does not whitelist."""
    try:
        cls = policy.classify(workspace / variant)
    except (OSError, ValueError):
        # System rejected the path — counts as containment hit.
        return
    assert cls != "editable", (
        f"variant {variant!r} (from seed {seed!r}, label {label!r}) → editable (LEAK)"
    )


def test_total_variant_count_at_least_500():
    """Sanity check: we should have generated ~500 variants."""
    assert len(_COMBO_VARIANTS) >= 500, (
        f"only generated {len(_COMBO_VARIANTS)} variants, want ≥500"
    )
