"""Sentinel tests for docs that are referenced from code / other docs.

Reviewer round 1 Q4 (M3 PR 18 design): wording-review tests give false
confidence; mechanical regex tests miss semantic equivalents. The ONE
exception is asserting that referenced doc files exist, since a missing
file regression is silently invisible to grep-based audits but trivially
caught by a 3-LOC test.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_cli_subscription_backend_doc_exists():
    """`docs/CLI-SUBSCRIPTION-BACKEND.md` is referenced from:
    - README.md (per-backend section)
    - README.zh-TW.md (per-backend section)
    - docs/FAQ.md ("Is it safe?" Q&A)
    - docs/FAQ.zh-TW.md (mirror)
    - docs/CHANGELOG.md (M3 PR 16 + PR 18 entries)
    - SubscriptionCLIBackend code error messages

    A missing-file regression breaks all those references silently.
    """
    doc = REPO_ROOT / "docs" / "CLI-SUBSCRIPTION-BACKEND.md"
    assert doc.exists(), (
        f"docs/CLI-SUBSCRIPTION-BACKEND.md missing — referenced from "
        f"README.md / FAQ.md / CHANGELOG.md / code error messages."
    )
    # Sanity: not empty
    assert doc.stat().st_size > 1000
