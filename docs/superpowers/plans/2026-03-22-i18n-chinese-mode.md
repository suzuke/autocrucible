# i18n Chinese Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add gettext-based i18n to crucible CLI, with Traditional Chinese (zh-TW) as first translation.

**Architecture:** New `i18n.py` module handles locale detection and gettext initialization. Every module with user-facing strings imports `_()` and wraps translatable strings. Translation files live in `src/crucible/locales/`. Hatchling `force-include` ensures locale files ship with the package.

**Tech Stack:** Python stdlib `gettext`, GNU gettext tools (`xgettext`, `msgfmt`, `msgmerge`)

**Spec:** `docs/superpowers/specs/2026-03-22-i18n-chinese-mode-design.md`

---

### Task 1: Create i18n module with tests

**Files:**
- Create: `src/crucible/i18n.py`
- Create: `tests/test_i18n.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write conftest.py to pin locale for all tests**

```python
# tests/conftest.py
import os


def pytest_configure(config):
    """Pin locale to English for deterministic test output.

    IMPORTANT: crucible.i18n binds _() at import time. This hook runs before
    any test module is collected, ensuring the first import of crucible.i18n
    sees CRUCIBLE_LANG=en. Do NOT add `from crucible.i18n import _` to
    crucible/__init__.py — that would import before this hook runs.
    """
    os.environ["CRUCIBLE_LANG"] = "en"
```

- [ ] **Step 2: Write failing tests for i18n module**

```python
# tests/test_i18n.py
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'crucible.i18n'`

- [ ] **Step 4: Implement i18n module**

```python
# src/crucible/i18n.py
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/crucible/i18n.py tests/test_i18n.py tests/conftest.py
git commit -m "feat(i18n): add i18n module with locale detection"
```

---

### Task 2: Create locale directory structure and .pot template

**Files:**
- Create: `src/crucible/locales/zh_TW/LC_MESSAGES/` (directory)
- Create: `src/crucible/locales/crucible.pot` (will be generated later, after all strings are wrapped)

- [ ] **Step 1: Create locale directory structure**

```bash
mkdir -p src/crucible/locales/zh_TW/LC_MESSAGES
```

- [ ] **Step 2: Commit**

```bash
git add src/crucible/locales/
git commit -m "feat(i18n): add locale directory structure"
```

---

### Task 3: Wrap cli.py strings with _()

This is the largest module. All `click.echo`, `click.secho`, `click.prompt`, command `help=` and docstrings need wrapping.

**Files:**
- Modify: `src/crucible/cli.py`

**Key rules:**
- Add `from crucible.i18n import _` at top
- f-strings become `_("text {var}").format(var=var)`
- Click command docstrings become `help=_("...")` parameter
- Click option/argument `help=` strings get wrapped: `help=_("...")`
- Table headers like `f"{'Commit':<10}"` — wrap only the label: `f"{_('Commit'):<10}"`
- Status keywords in log lines (`keep`/`discard`/`crash`) stay English
- Shell commands in output (e.g., `"  uv sync"`, `"  crucible init --tag run1"`) stay English — they are commands, not prose

- [ ] **Step 1: Add import and wrap main command group + `new` command strings**

Add `from crucible.i18n import _` to imports.

Wrap: main group docstring, `new` command help, all `click.echo`/`click.secho` in `new` command. Keep shell command strings (`"  cd {dest_path}"`, `"  uv sync"`, etc.) unwrapped.

- [ ] **Step 2: Wrap `init` command strings**

Wrap: command help, option help texts, all echo/secho messages.

- [ ] **Step 3: Wrap `run` command strings**

Wrap: command help, option help texts, all echo/secho/prompt messages. Keep `"Fork from"` prompt wrapped.

- [ ] **Step 4: Wrap `status` command strings**

Wrap: command help, option help texts, all output messages. For cost/metric lines, wrap the template but keep `$` and numeric formatting.

- [ ] **Step 5: Wrap `validate` command strings**

Wrap: command help, option help texts, PASS/FAIL/WARN labels, status messages. Keep `[PASS]`/`[FAIL]`/`[WARN]` prefix unwrapped (they are icons, not prose).

- [ ] **Step 6: Wrap `history` command strings**

Wrap: command help, option help texts, "No results yet.", table headers (`Commit`, `Metric`, `Status`, `Description`).

- [ ] **Step 7: Wrap `compare` command strings**

Wrap: command help, option help texts, error messages, comparison table labels (e.g., `"Total"`, `"Kept"`, `"Best"`).

- [ ] **Step 8: Wrap `wizard` command strings**

Wrap: command help, option help texts, interactive prompts, status messages, "Next steps:" etc.

- [ ] **Step 9: Wrap token profile strings**

Wrap: "No iterations to analyze.", section headers (`"Token Profile"`, `"Prompt Breakdown"`, `"Cache Efficiency"`), table headers (`Iter`, `In Tok`, `Out Tok`, etc.).

- [ ] **Step 10: Wrap `postmortem` and `update` command strings**

Wrap: command help, option help texts, error/status messages.

- [ ] **Step 11: Run existing tests to verify nothing is broken**

Run: `uv run pytest tests/test_cli.py -v`
Expected: All PASS (conftest.py sets `CRUCIBLE_LANG=en`, gettext fallback returns English source strings)

- [ ] **Step 12: Commit**

```bash
git add src/crucible/cli.py
git commit -m "feat(i18n): wrap all cli.py strings with gettext _()"
```

---

### Task 4: Wrap orchestrator.py strings with _()

**Files:**
- Modify: `src/crucible/orchestrator.py`

**Key rules:**
- `logger.info/warning/error` messages get wrapped
- Log prefixes like `[iter N]`, `[beam-N]`, `[profile]` stay English
- The `_FATAL_MSG` constant gets wrapped
- Status keywords in iteration summary (`keep`/`discard`/`crash`) stay English (for `_ColorFormatter`)
- The restart feedback string (`"⟳ RESTART — ..."`) is agent-facing context — stays English

- [ ] **Step 1: Add import and wrap all user-facing logger messages**

Add `from crucible.i18n import _`. Wrap messages like `"Budget exceeded — stopping"`, `"Reached max iterations..."`, `"Stopped after N iterations."`, `"Installing updated requirements..."`, etc.

Keep English: `[iter N]` prefix, status keywords, agent-facing restart message.

- [ ] **Step 2: Run existing tests**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/crucible/orchestrator.py
git commit -m "feat(i18n): wrap orchestrator.py strings with gettext _()"
```

---

### Task 5: Wrap preflight.py strings with _()

**Files:**
- Modify: `src/crucible/preflight.py`

- [ ] **Step 1: Add import and wrap all error/warning messages**

Wrap all multiline error messages (install instructions, auth errors). Keep shell commands (`claude login`, `npm install -g @anthropic-ai/claude-code`) unwrapped inside the strings — they are commands.

- [ ] **Step 2: Run existing tests**

Run: `uv run pytest tests/test_preflight.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/crucible/preflight.py
git commit -m "feat(i18n): wrap preflight.py strings with gettext _()"
```

---

### Task 6: Wrap validator.py strings with _()

**Files:**
- Modify: `src/crucible/validator.py`

- [ ] **Step 1: Add import and wrap all check names and messages**

Wrap check names (`"Config"`, `"Docker"`, `"Instructions"`, etc.) and result messages. Keep technical output like `f"CV={stability.cv:.1f}%"` formatting intact.

- [ ] **Step 2: Run existing tests**

Run: `uv run pytest tests/test_validator.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/crucible/validator.py
git commit -m "feat(i18n): wrap validator.py strings with gettext _()"
```

---

### Task 7: Wrap guardrails.py strings with _()

**Files:**
- Modify: `src/crucible/guardrails.py`

**Strategy:** `Violation.message` is consumed by both (a) the orchestrator's logger (user-facing) and (b) `context.py` as agent error feedback. Per spec, agent feedback must stay English. Therefore: do NOT wrap `Violation.message` with `_()`. Keep it English. Only wrap at the display site — in `cli.py`'s `validate` command where violations are printed. The orchestrator logger also shows violations, but since the same text feeds the agent, keeping it English there is the safer choice.

- [ ] **Step 1: Verify that Violation.message stays English (no wrapping)**

Do NOT add `_()` to `guardrails.py`. Instead, if `cli.py`'s validate command displays violation messages, wrap only at that display point.

- [ ] **Step 2: Run existing tests**

Run: `uv run pytest tests/test_guardrails.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/crucible/guardrails.py
git commit -m "feat(i18n): wrap guardrails.py strings with gettext _()"
```

---

### Task 8: Wrap wizard.py strings with _()

**Files:**
- Modify: `src/crucible/wizard.py`

**Key rules:**
- `ANALYZE_SYSTEM_PROMPT` and `GENERATE_SYSTEM_PROMPT` are agent-facing — stay English
- `_format_environment()` labels are user-facing — wrap
- Error messages from `_call_claude()` and `ExperimentWizard` — wrap
- Placeholder detection error messages — wrap

- [ ] **Step 1: Add import and wrap user-facing strings**

- [ ] **Step 2: Run existing tests**

Run: `uv run pytest tests/test_wizard.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/crucible/wizard.py
git commit -m "feat(i18n): wrap wizard.py strings with gettext _()"
```

---

### Task 9: Wrap postmortem.py strings with _()

**Files:**
- Modify: `src/crucible/postmortem.py`

**Key rules:**
- `render_text()` section headers and labels — wrap
- `_build_insights_prompt()` — stays English (agent-facing)
- Error fallback message `"(AI analysis unavailable: ...)"` — wrap

- [ ] **Step 1: Add import and wrap render_text() strings**

- [ ] **Step 2: Run existing tests**

Run: `uv run pytest tests/test_postmortem.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/crucible/postmortem.py
git commit -m "feat(i18n): wrap postmortem.py strings with gettext _()"
```

---

### Task 10: Wrap sandbox.py strings with _()

**Files:**
- Modify: `src/crucible/sandbox.py`

Only 2 user-facing strings: `"Building Docker image {tag}..."` and `"Docker build failed: ..."`.

- [ ] **Step 1: Add import and wrap the 2 logger messages**

- [ ] **Step 2: Run existing tests**

Run: `uv run pytest tests/test_sandbox.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/crucible/sandbox.py
git commit -m "feat(i18n): wrap sandbox.py strings with gettext _()"
```

---

### Task 11: Extract .pot template and create zh-TW translation

**Files:**
- Create: `src/crucible/locales/crucible.pot`
- Create: `src/crucible/locales/zh_TW/LC_MESSAGES/crucible.po`
- Create: `src/crucible/locales/zh_TW/LC_MESSAGES/crucible.mo`

- [ ] **Step 1: Extract .pot template from source**

```bash
cd /path/to/crucible
xgettext -L Python -o src/crucible/locales/crucible.pot \
  --from-code=UTF-8 --keyword=_ \
  $(find src/crucible -name '*.py' ! -path '*/examples/*')
```

Verify the .pot file contains all expected msgid strings.

- [ ] **Step 2: Initialize zh_TW .po file from .pot**

```bash
msginit -i src/crucible/locales/crucible.pot \
  -o src/crucible/locales/zh_TW/LC_MESSAGES/crucible.po \
  -l zh_TW --no-translator
```

- [ ] **Step 3: Translate all msgstr entries to Traditional Chinese**

Edit `crucible.po` and fill in all `msgstr` entries. Key translation guidelines:
- Technical terms (git, commit, metric, CLI, etc.) keep English
- Shell commands stay as-is
- Format placeholders (`{var}`) must be preserved exactly
- Use natural Taiwanese Mandarin, not formal/academic Chinese
- Examples:
  - `"No results yet."` → `"尚無結果。"`
  - `"Budget exceeded — stopping"` → `"預算已超出 — 停止"`
  - `"Show summary of experiment results."` → `"顯示實驗結果摘要。"`
  - `"Press Ctrl+C to stop gracefully."` → `"按 Ctrl+C 優雅停止。"`
  - `"Validation failed."` → `"驗證失敗。"`

- [ ] **Step 4: Validate format strings and compile .mo binary**

```bash
# Validate that all {var} placeholders match between msgid and msgstr
msgfmt --check src/crucible/locales/zh_TW/LC_MESSAGES/crucible.po

# Compile
msgfmt -o src/crucible/locales/zh_TW/LC_MESSAGES/crucible.mo \
  src/crucible/locales/zh_TW/LC_MESSAGES/crucible.po
```

If `--check` reports format string mismatches, fix the .po file before compiling.

- [ ] **Step 5: Commit**

```bash
git add src/crucible/locales/
git commit -m "feat(i18n): add zh-TW translation for all CLI strings"
```

---

### Task 12: Add translation integration test

**Files:**
- Modify: `tests/test_i18n.py`

- [ ] **Step 1: Write integration test that verifies zh-TW translation loads**

```python
class TestTranslationLoading:
    def test_zh_tw_translation_loads(self):
        """Verify the .mo file loads and returns Chinese strings."""
        import gettext
        from pathlib import Path

        locales_dir = Path(__file__).parent.parent / "src" / "crucible" / "locales"
        translation = gettext.translation(
            "crucible",
            localedir=str(locales_dir),
            languages=["zh_TW"],
            fallback=False,  # Must NOT fallback — .mo must exist
        )
        result = translation.gettext("No results yet.")
        assert result != "No results yet."  # Must be translated
        assert len(result) > 0

    def test_en_fallback_returns_source(self):
        """English locale uses source strings (no .mo needed)."""
        import gettext
        from pathlib import Path

        locales_dir = Path(__file__).parent.parent / "src" / "crucible" / "locales"
        translation = gettext.translation(
            "crucible",
            localedir=str(locales_dir),
            languages=["en"],
            fallback=True,
        )
        result = translation.gettext("No results yet.")
        assert result == "No results yet."

    def test_module_level_underscore_with_zh_tw(self):
        """End-to-end: verify _() returns Chinese when module reloaded with zh-TW locale."""
        import importlib
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
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_i18n.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_i18n.py
git commit -m "test(i18n): add translation loading integration tests"
```

---

### Task 13: Update packaging config

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add locale files to hatchling force-include**

Add to the existing `[tool.hatch.build.targets.wheel.force-include]` section:

```toml
"src/crucible/locales" = "crucible/locales"
```

- [ ] **Step 2: Verify package includes locales**

```bash
uv build
# Check the wheel contents
python3 -c "import zipfile; [print(n) for n in zipfile.ZipFile('dist/autocrucible-*.whl').namelist() if 'locales' in n]"
```

Expected: `crucible/locales/zh_TW/LC_MESSAGES/crucible.mo` should appear.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: include locale files in wheel package"
```

---

### Task 14: Run full test suite and verify

- [ ] **Step 1: Run all tests**

Run: `uv run pytest -v`
Expected: All PASS. The `conftest.py` ensures `CRUCIBLE_LANG=en` so all existing assertions against English strings still hold.

- [ ] **Step 2: Manual smoke test in Chinese**

```bash
CRUCIBLE_LANG=zh-TW crucible --help
CRUCIBLE_LANG=zh-TW crucible new --list
CRUCIBLE_LANG=zh-TW crucible status --help
```

Verify output is in Traditional Chinese.

- [ ] **Step 3: Manual smoke test in English**

```bash
CRUCIBLE_LANG=en crucible --help
```

Verify output is still in English.

- [ ] **Step 4: Final commit if any fixes needed**

---

### Task 15: Update documentation

**Files:**
- Modify: `README.md` — mention i18n support and `CRUCIBLE_LANG` env var
- Modify: `README.zh-TW.md` — same in Chinese

- [ ] **Step 1: Add i18n section to README.md**

Add a brief section under an appropriate heading:

```markdown
## Language / 語言

Crucible auto-detects your system locale. To override:

```bash
export CRUCIBLE_LANG=zh-TW   # Traditional Chinese
export CRUCIBLE_LANG=en       # English (default)
```
```

- [ ] **Step 2: Add same section to README.zh-TW.md**

- [ ] **Step 3: Commit**

```bash
git add README.md README.zh-TW.md
git commit -m "docs: add i18n language configuration to README"
```
