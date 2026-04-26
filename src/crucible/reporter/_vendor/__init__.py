"""Vendored third-party assets for the reporter.

Currently:
  - `d3.v7.9.0.min.js` — d3.js v7.9.0 (ISC License, Mike Bostock)
    See `LICENSE-d3.txt` for full attribution.

Loaded via `importlib.resources` by `interactive.py`. The vendored
file is shipped verbatim — never modified — to keep the license
attribution clean and updates trivial (just replace the file).

Reviewer round 2 polish: SHA-256 of the upstream-fetched bytes is
pinned below for tamper-detection in CI. If the file is ever updated
in-place without bumping `D3_VERSION`, the integrity test trips.
"""

from __future__ import annotations

import hashlib
import importlib.resources

D3_VERSION = "7.9.0"
D3_FILENAME = f"d3.v{D3_VERSION}.min.js"

# SHA-256 of the verbatim bytes fetched from
# https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js
# Computed on 2026-04-26. If you bump D3_VERSION, also update this.
D3_SHA256 = "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539"


def read_d3_source() -> str:
    """Return the vendored d3 minified source as a UTF-8 string."""
    return importlib.resources.files(__package__).joinpath(D3_FILENAME).read_text(
        encoding="utf-8"
    )


def verify_d3_integrity() -> bool:
    """Verify the vendored d3 file matches the pinned SHA-256.

    Returns True iff the on-disk bytes hash to `D3_SHA256`. CI can call
    this to detect accidental in-place edits to the vendored file.
    """
    raw = importlib.resources.files(__package__).joinpath(D3_FILENAME).read_bytes()
    return hashlib.sha256(raw).hexdigest() == D3_SHA256
