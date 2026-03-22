import os


def pytest_configure(config):
    """Pin locale to English for deterministic test output.

    IMPORTANT: crucible.i18n binds _() at import time. This hook runs before
    any test module is collected, ensuring the first import of crucible.i18n
    sees CRUCIBLE_LANG=en. Do NOT add `from crucible.i18n import _` to
    crucible/__init__.py — that would import before this hook runs.
    """
    os.environ["CRUCIBLE_LANG"] = "en"
