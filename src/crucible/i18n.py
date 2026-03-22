"""Internationalization support for crucible CLI."""

from __future__ import annotations

import gettext
import os
from pathlib import Path

_LOCALES_DIR = Path(__file__).parent / "locales"


def get_locale() -> str:
    """Detect locale: CRUCIBLE_LANG > LC_ALL > LC_MESSAGES > LANG > 'en'."""
    lang = (
        os.environ.get("CRUCIBLE_LANG")
        or os.environ.get("LC_ALL")
        or os.environ.get("LC_MESSAGES")
        or os.environ.get("LANG", "en")
    )
    if lang.startswith("zh_TW") or lang.lower() == "zh-tw":
        return "zh_TW"
    return "en"


_locale = get_locale()
_translation = gettext.translation(
    "crucible",
    localedir=str(_LOCALES_DIR),
    languages=[_locale],
    fallback=True,
)
_ = _translation.gettext
