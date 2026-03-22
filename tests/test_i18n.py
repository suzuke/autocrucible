import gettext
import importlib
import os
from pathlib import Path
from unittest.mock import patch


class TestGetLocale:
    def test_crucible_lang_override(self):
        with patch.dict(os.environ, {"CRUCIBLE_LANG": "zh-TW"}, clear=False):
            from crucible.i18n import get_locale
            assert get_locale() == "zh_TW"

    def test_lc_all_zh_tw(self):
        env = {"LC_ALL": "zh_TW.UTF-8"}
        with patch.dict(os.environ, env, clear=True):
            from crucible.i18n import get_locale
            assert get_locale() == "zh_TW"

    def test_lc_messages_zh_tw(self):
        env = {"LC_MESSAGES": "zh_TW.UTF-8"}
        with patch.dict(os.environ, env, clear=True):
            from crucible.i18n import get_locale
            assert get_locale() == "zh_TW"

    def test_lang_en(self):
        env = {"LANG": "en_US.UTF-8"}
        with patch.dict(os.environ, env, clear=True):
            from crucible.i18n import get_locale
            assert get_locale() == "en"

    def test_fallback_to_en(self):
        with patch.dict(os.environ, {}, clear=True):
            from crucible.i18n import get_locale
            assert get_locale() == "en"

    def test_crucible_lang_overrides_lc_all(self):
        env = {"CRUCIBLE_LANG": "en", "LC_ALL": "zh_TW.UTF-8"}
        with patch.dict(os.environ, env, clear=False):
            from crucible.i18n import get_locale
            assert get_locale() == "en"

    def test_posix_priority_lc_all_over_lang(self):
        env = {"LC_ALL": "zh_TW.UTF-8", "LANG": "en_US.UTF-8"}
        with patch.dict(os.environ, env, clear=True):
            from crucible.i18n import get_locale
            assert get_locale() == "zh_TW"


class TestTranslationLoading:
    """Test that .mo files load correctly."""

    _locales_dir = Path(__file__).parent.parent / "src" / "crucible" / "locales"

    def test_zh_tw_translation_loads(self):
        """Verify the .mo file loads and returns Chinese strings."""
        translation = gettext.translation(
            "crucible",
            localedir=str(self._locales_dir),
            languages=["zh_TW"],
            fallback=False,
        )
        result = translation.gettext("No results yet.")
        assert result != "No results yet."
        assert len(result) > 0

    def test_en_fallback_returns_source(self):
        """English locale uses source strings (no .mo needed)."""
        translation = gettext.translation(
            "crucible",
            localedir=str(self._locales_dir),
            languages=["en"],
            fallback=True,
        )
        result = translation.gettext("No results yet.")
        assert result == "No results yet."

    def test_module_level_underscore_with_zh_tw(self):
        """End-to-end: verify _() returns Chinese when module reloaded with zh-TW locale."""
        import crucible.i18n

        with patch.dict(os.environ, {"CRUCIBLE_LANG": "zh-TW"}, clear=False):
            importlib.reload(crucible.i18n)
            try:
                result = crucible.i18n._("No results yet.")
                assert result != "No results yet."
                assert len(result) > 0
            finally:
                # Restore English locale for remaining tests
                with patch.dict(os.environ, {"CRUCIBLE_LANG": "en"}, clear=False):
                    importlib.reload(crucible.i18n)
