import os
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
