"""POC Day 1 — LocalPythonExecutor escape spike.

GOAL: empirically test whether smolagents' `LocalPythonExecutor` with
`additional_authorized_imports=[]` blocks ~50 known Python sandbox-escape
techniques.

DECISION GATE: this test's outcome determines whether Docker is mandatory in
default safe mode. Per HF official docs, LocalPythonExecutor is documented as
"best-effort mitigation, not a security boundary" — we expect SOME of these
patterns to escape. The point is to document WHICH ones, so:

  (a) The "Docker is mandatory" decision in v3.2 is empirically grounded.
  (b) Layer 2 (LocalPythonExecutor) is correctly described as "blast radius
      reducer / accidental misuse guard", not "sandbox".
  (c) The patterns that DO escape become a known issue tracker for upstream
      reports / future hardening.

Each test asserts the executor RAISES on the attack input. A test FAILURE
means the attack succeeded → executor cannot be trusted as a boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from smolagents.local_python_executor import LocalPythonExecutor


@pytest.fixture
def executor() -> LocalPythonExecutor:
    """LocalPythonExecutor with the strictest M1 default config.

    Note: smolagents' LocalPythonExecutor requires `send_tools({})` to load the
    BASE_PYTHON_TOOLS (range, list, len, etc.). Without this, even basic Python
    constructs fail. We pass an empty tools dict to mimic the realistic agent
    setup where Crucible's whitelisted tools have not yet been registered.
    """
    ex = LocalPythonExecutor(
        additional_authorized_imports=[],
        additional_functions=None,
        max_print_outputs_length=None,
    )
    ex.send_tools({})
    return ex


def _run(executor: LocalPythonExecutor, code: str) -> tuple[bool, str]:
    """Run `code` and return (was_blocked, message).

    was_blocked = True if the executor refused to run the code.
    message = exception type + message OR successful output.
    """
    try:
        result = executor(code)
        return False, f"OK: {result}"
    except Exception as exc:
        return True, f"{type(exc).__name__}: {str(exc)[:200]}"


# ---------------------------------------------------------------------------
# Family A — direct imports of dangerous modules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import sys",
        "import subprocess",
        "import importlib",
        "import builtins",
        "import ctypes",
        "import socket",
        "import urllib.request",
        "import http.client",
        "import shutil",
        "import pathlib",
        "from os import system",
        "from subprocess import run",
        "from importlib import import_module",
    ],
)
def test_dangerous_import_blocked(executor, code):
    blocked, msg = _run(executor, code)
    assert blocked, f"ESCAPE: {code!r} succeeded — {msg}"


# ---------------------------------------------------------------------------
# Family B — dynamic / indirect imports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "__import__('os')",
        "__import__('os').system('ls')",
        "__import__('subprocess').run(['ls'])",
        "x = __import__('os'); x.system('ls')",
        "for m in ['os', 'subprocess']: __import__(m)",
        "exec('import os')",
        "eval('__import__(\"os\")')",
        "compile('import os', '<string>', 'exec')",
    ],
)
def test_dynamic_import_blocked(executor, code):
    blocked, msg = _run(executor, code)
    assert blocked, f"ESCAPE: {code!r} succeeded — {msg}"


# ---------------------------------------------------------------------------
# Family C — class-introspection escapes (the classic ones)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        # Walk subclasses of object to find os/subprocess
        "().__class__.__bases__[0].__subclasses__()",
        "().__class__.__mro__[1].__subclasses__()",
        "(1).__class__.__base__.__subclasses__()",
        "''.__class__.__mro__[1].__subclasses__()",
        # Look up __builtins__ via class hierarchy
        "().__class__.__bases__[0].__subclasses__()[0].__init__.__globals__",
        "[].__class__.__bases__[0].__subclasses__()",
        # globals() / locals() probing
        "globals()",
        "globals()['__builtins__']",
        "locals()",
        # Builtins access
        "__builtins__",
        "__builtins__.open",
        "getattr(__builtins__, 'open')",
        "vars(__builtins__)",
        "dir(__builtins__)",
    ],
)
def test_class_introspection_blocked(executor, code):
    blocked, msg = _run(executor, code)
    assert blocked, f"ESCAPE: {code!r} succeeded — {msg}"


# ---------------------------------------------------------------------------
# Family D — direct dangerous function calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "open('/etc/passwd')",
        "open('/etc/passwd').read()",
        "exec('print(1)')",
        "eval('1+1')",  # eval may or may not be allowed; we want it blocked in M1
        "compile('1+1', '<x>', 'eval')",
        "exit()",
        "quit()",
        "input()",
        "breakpoint()",
        "help()",
    ],
)
def test_dangerous_builtin_call_blocked(executor, code):
    blocked, msg = _run(executor, code)
    assert blocked, f"ESCAPE: {code!r} succeeded — {msg}"


# ---------------------------------------------------------------------------
# Family E — getattr / setattr fiddling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "getattr(__builtins__, 'open')",
        "getattr(__builtins__, '__import__')",
        "getattr({}, '__class__').__bases__",
        "type(())(1, 2, 3)",  # construct via type
        "().__getattribute__('__class__')",
        "().__getattribute__('__class__').__getattribute__(().__class__, '__bases__')",
    ],
)
def test_getattr_fiddling_blocked(executor, code):
    blocked, msg = _run(executor, code)
    assert blocked, f"ESCAPE: {code!r} succeeded — {msg}"


# ---------------------------------------------------------------------------
# Family E.5 — patterns that PASS through executor but are HARMLESS
# (introspection that returns class info but cannot weaponize it inside the executor)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected_substr",
    [
        ("type(())", "tuple"),
        ("getattr({}, '__class__')", "dict"),
    ],
)
def test_introspection_allowed_but_inert(executor, code, expected_substr):
    """Some introspection (type(), .__class__) is allowed by smolagents
    because the returned class object cannot be used for further escape
    (subscript/getattr chains are intercepted). Document this behavior."""
    blocked, msg = _run(executor, code)
    if blocked:
        # If smolagents tightens this, that's fine too — the test is informational.
        return
    assert expected_substr in msg.lower(), f"unexpected output: {msg}"


# ---------------------------------------------------------------------------
# Family F — multi-step / disguised escapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        # Hide the import in a string
        "name = 'o' + 's'\n__import__(name)",
        "modules = ['o' + 's', 'sub' + 'process']\nfor m in modules: __import__(m)",
        # Use chr() to construct module name
        "__import__(chr(111)+chr(115))",  # 'os'
        # Disguise via base64
        "import base64\nexec(base64.b64decode('aW1wb3J0IG9z'))",
        # Multi-line setup
        "x = ()\ny = x.__class__\nz = y.__bases__\nw = z[0]\nv = w.__subclasses__()",
        # Generator-based
        "list(__import__(m) for m in ['os'])",
        # Class definition trying to escape via __init_subclass__
        # (the actual __import__ is triggered when a subclass is created)
        "class Evil:\n    def __init_subclass__(cls):\n        __import__('os')\nclass Sub(Evil):\n    pass",
    ],
)
def test_disguised_escape_blocked(executor, code):
    blocked, msg = _run(executor, code)
    assert blocked, f"ESCAPE: {code!r} succeeded — {msg}"


# ---------------------------------------------------------------------------
# Family F.5 — class-body / decorator escapes neutered at execution
# (the SHAPE is allowed but the dangerous body cannot achieve real side effects)
# ---------------------------------------------------------------------------


def test_metaclass_body_does_not_escape(executor, tmp_path):
    """A metaclass whose __init__ tries to spawn a subprocess MUST NOT actually
    succeed in writing to disk via host os. smolagents allows the class to be
    defined but the body's __import__ is neutered.

    Verification: try to create a sentinel file; assert it does NOT exist after.
    """
    sentinel = tmp_path / "PWN_PROOF_META"
    code = f"""class Meta(type):
    def __init__(cls, *a, **kw):
        os_module = __import__('os')
        os_module.system('touch {sentinel}')

class Evil(metaclass=Meta):
    pass
"""
    blocked, msg = _run(executor, code)
    # Either the executor blocks it outright (good), or it allows the definition
    # but neuters __import__ (also fine — no real side effect).
    assert not sentinel.exists(), f"REAL ESCAPE: sentinel {sentinel} was created — msg: {msg}"


def test_decorator_body_does_not_escape(executor, tmp_path):
    sentinel = tmp_path / "PWN_PROOF_DECO"
    code = f"""def deco(f):
    os_module = __import__('os')
    os_module.system('touch {sentinel}')
    return f

@deco
def noop():
    pass
"""
    blocked, msg = _run(executor, code)
    assert not sentinel.exists(), f"REAL ESCAPE: sentinel {sentinel} was created — msg: {msg}"


def test_init_subclass_body_does_not_escape(executor, tmp_path):
    sentinel = tmp_path / "PWN_PROOF_INITSUB"
    code = f"""class Evil:
    def __init_subclass__(cls):
        os_module = __import__('os')
        os_module.system('touch {sentinel}')

class Sub(Evil):
    pass
"""
    blocked, msg = _run(executor, code)
    assert not sentinel.exists(), f"REAL ESCAPE: sentinel {sentinel} was created — msg: {msg}"


# ---------------------------------------------------------------------------
# Family G — file-system writes that DON'T need imports
# (these are about whether basic Python can touch the filesystem at all)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "open('/tmp/escape.txt', 'w').write('pwned')",
        "with open('/tmp/escape.txt', 'w') as f: f.write('pwned')",
    ],
)
def test_file_write_blocked(executor, code):
    blocked, msg = _run(executor, code)
    assert blocked, f"ESCAPE: {code!r} succeeded — {msg}"


# ---------------------------------------------------------------------------
# Family H — sanity (these MUST work — confirm the executor isn't blocking everything)
# ---------------------------------------------------------------------------


def test_arithmetic_works(executor):
    blocked, msg = _run(executor, "1 + 1")
    assert not blocked, f"basic arithmetic raised: {msg}"


def test_authorized_import_math_works(executor):
    """`math` is in the default authorized list, should work."""
    blocked, msg = _run(executor, "import math\nmath.sqrt(4)")
    assert not blocked, f"math.sqrt should work: {msg}"


def test_string_manipulation_works(executor):
    blocked, msg = _run(executor, "x = 'hello'; y = x.upper(); y")
    assert not blocked, f"string ops raised: {msg}"


def test_list_comprehension_works(executor):
    blocked, msg = _run(executor, "[i*2 for i in range(5)]")
    assert not blocked, f"list comp raised: {msg}"
