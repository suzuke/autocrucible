"""Vendored third-party assets for the reporter.

Currently:
  - `d3.v7.9.0.min.js` — d3.js v7.9.0 (ISC License, Mike Bostock)
    See `LICENSE-d3.txt` for full attribution.

Loaded via `importlib.resources` by `interactive.py`. The vendored
file is shipped verbatim — never modified — to keep the license
attribution clean and updates trivial (just replace the file).
"""

from __future__ import annotations

import importlib.resources

D3_VERSION = "7.9.0"
D3_FILENAME = f"d3.v{D3_VERSION}.min.js"


def read_d3_source() -> str:
    """Return the vendored d3 minified source as a UTF-8 string."""
    return importlib.resources.files(__package__).joinpath(D3_FILENAME).read_text(
        encoding="utf-8"
    )
