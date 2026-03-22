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
| `orchestrator.py` | Iteration status lines, stop messages, error feedback, budget warnings |
| `preflight.py` | Auth errors, fix suggestions |
| `validator.py` | Validation pass/fail messages |
| `guardrails.py` | Violation messages |
| `postmortem.py` | Analysis output |

### Out of scope (keep English)

| What | Why |
|------|-----|
| `context.py` agent prompts | Given to Claude, not the user; English performs better |
| `results.tsv` status values (`keep`/`discard`/`crash`) | Machine-readable data, must stay consistent |
| Log prefixes (`[iter 5]`, `[beam-2]`, `[profile]`) | Technical identifiers |
| `results.tsv` headers | Data format, not UI |

## Design

### New module: `src/crucible/i18n.py`

Responsibilities:
1. Detect locale from environment
2. Initialize gettext
3. Export `_()` translation function

```python
import gettext
import os
from pathlib import Path

_LOCALES_DIR = Path(__file__).parent / "locales"

def get_locale() -> str:
    """Detect locale: CRUCIBLE_LANG > LANG/LC_ALL > fallback 'en'."""
    lang = os.environ.get("CRUCIBLE_LANG") or os.environ.get("LANG", "en")
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

For Click command docstrings, use lazy evaluation or set help text explicitly:

```python
@cli.command()
@click.pass_context
def status(ctx):
    """Show summary of experiment results."""  # English stays as default
    ...

# Becomes:
@cli.command(help=_("Show summary of experiment results."))
@click.pass_context
def status(ctx):
    ...
```

### Locale detection logic

Priority chain:
1. `CRUCIBLE_LANG` environment variable (explicit override)
2. `LANG` / `LC_ALL` system locale
3. Fallback: `en`

Mapping:
- `zh_TW*`, `zh-TW` → `zh_TW`
- Everything else → `en` (fallback, English source strings used directly)

### Packaging

`pyproject.toml` must include locale files in the package:

```toml
[tool.setuptools.package-data]
crucible = ["locales/**/*"]
```

### Development workflow

1. Mark all strings with `_()`
2. Extract template: `xgettext -o src/crucible/locales/crucible.pot src/crucible/*.py`
3. Update .po: `msgmerge -U locales/zh_TW/LC_MESSAGES/crucible.po locales/crucible.pot`
4. Translate strings in `crucible.po`
5. Compile: `msgfmt -o crucible.mo crucible.po`
6. Commit both `.po` and `.mo`

### Testing

- Test `get_locale()` with mocked environment variables
- Test that `_()` returns Chinese strings when locale is `zh_TW`
- Test fallback to English when locale is unrecognized
- Test that `CRUCIBLE_LANG` overrides `LANG`

## Constraints

- No new dependencies (gettext is stdlib)
- Agent-facing prompts in `context.py` stay English
- Machine-readable data formats unchanged
- Existing tests must pass without modification (they run in English locale)
