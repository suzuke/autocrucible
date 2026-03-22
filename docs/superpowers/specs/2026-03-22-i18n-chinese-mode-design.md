# Crucible i18n: Chinese (zh-TW) Mode

## Summary

Add internationalization (i18n) to crucible's CLI output using Python's built-in `gettext`. Users see output in their system language automatically, with `CRUCIBLE_LANG` as an override. First supported translation: Traditional Chinese (zh-TW).

## Motivation

- Crucible's author and a significant portion of potential users are Traditional Chinese speakers
- CLI output is entirely English — error messages, help text, and status displays would be more accessible in the user's native language
- README already has a zh-TW version; CLI output should match

## Scope

### In scope (translate)

All user-facing CLI output across every module:

| Module | What to translate |
|--------|-------------------|
| `cli.py` | Command docstrings, `--help` text, interactive prompts, status displays, wizard questions, profile output, update messages |
| `orchestrator.py` | User-facing `logger.*()` messages: iteration status, stop messages, budget warnings. Note: `_ColorFormatter` pattern-matches on status keywords (`keep`/`discard`/`crash`) — these keywords stay English in the log prefix, translation applies to surrounding text only |
| `preflight.py` | Auth errors, fix suggestions |
| `validator.py` | Validation pass/fail messages |
| `guardrails.py` | Only messages shown directly to the user via CLI. Violation messages fed back to the agent (via `context.py`) stay English |
| `wizard.py` | Interactive prompts, status messages, next-steps guidance |
| `postmortem.py` | `render_text()` output (section headers, labels). `_build_insights_prompt()` stays English (agent-facing) |

### Out of scope (keep English)

| What | Why |
|------|-----|
| `context.py` agent prompts | Given to Claude, not the user; English performs better |
| `postmortem.py` `_build_insights_prompt()` | Agent-facing prompt, same reason as above |
| `guardrails.py` violation messages fed to agent | Part of error feedback loop to Claude |
| `results.tsv` status values (`keep`/`discard`/`crash`) | Machine-readable data, must stay consistent |
| Log prefixes (`[iter 5]`, `[beam-2]`, `[profile]`) | Technical identifiers; `_ColorFormatter` depends on them |
| `results.tsv` headers | Data format, not UI |

## Design

### New module: `src/crucible/i18n.py`

Responsibilities:
1. Detect locale from environment (POSIX-compliant priority)
2. Initialize gettext
3. Export `_()` translation function

```python
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

### Translation file structure

```
src/crucible/locales/
    crucible.pot                    # Template (extracted from source)
    zh_TW/
        LC_MESSAGES/
            crucible.po             # Human-readable translations
            crucible.mo             # Compiled binary (committed to repo)
```

`.mo` files are committed so users don't need to compile translations themselves.

### Per-module changes

Every module with user-facing strings gets:

```python
from crucible.i18n import _
```

All translatable strings wrapped with `_()`:

```python
# Before
click.echo("No results yet.")

# After
click.echo(_("No results yet."))
```

For strings with format variables:

```python
# Before
click.echo(f"Created empty project at {dest_path}")

# After
click.echo(_("Created empty project at {dest_path}").format(dest_path=dest_path))
```

For Click command docstrings, use `help=` parameter:

```python
@cli.command(help=_("Show summary of experiment results."))
@click.pass_context
def status(ctx):
    ...
```

Note: `_()` is evaluated at import time, which locks the locale. This is acceptable for a CLI (locale is determined once at startup). If `--lang` CLI flag is ever added, a `LazyString` wrapper would be needed.

### Locale detection logic

Priority chain (POSIX-compliant):
1. `CRUCIBLE_LANG` environment variable (explicit override)
2. `LC_ALL`
3. `LC_MESSAGES`
4. `LANG`
5. Fallback: `en`

Mapping:
- `zh_TW*`, `zh-TW` → `zh_TW`
- Everything else → `en` (fallback, English source strings used directly)

### Packaging

The project uses hatchling as build backend. Include locale files via:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/crucible/locales" = "crucible/locales"
```

### Development workflow

1. Mark all strings with `_()`
2. Extract template: `xgettext -L Python -o src/crucible/locales/crucible.pot $(find src/crucible -name '*.py')`
3. Create/update .po: `msgmerge -U src/crucible/locales/zh_TW/LC_MESSAGES/crucible.po src/crucible/locales/crucible.pot`
4. Translate strings in `crucible.po`
5. Compile: `msgfmt -o crucible.mo crucible.po`
6. Commit both `.po` and `.mo`

### Testing

- Test `get_locale()` with mocked environment variables
- Test that `_()` returns Chinese strings when locale is `zh_TW`
- Test fallback to English when locale is unrecognized
- Test that `CRUCIBLE_LANG` overrides `LANG` and `LC_ALL`
- `conftest.py` sets `CRUCIBLE_LANG=en` to guarantee deterministic test output regardless of developer locale

## Constraints

- No new dependencies (gettext is stdlib)
- Agent-facing prompts in `context.py`, `postmortem._build_insights_prompt()`, and guardrail violation messages fed to agent stay English
- Machine-readable data formats unchanged
- `_ColorFormatter` pattern matching on English status keywords preserved
- Existing tests pass via `CRUCIBLE_LANG=en` in conftest
