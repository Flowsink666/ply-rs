"""Backend selection, strict native mode, and observable setup failures."""

import os
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import rply.backends as backends
import rply.core.lexer_engine as lexer_module
from rply import Grammar, LexerEngine, LRParser, LRTable, Token

ROOT = Path(__file__).resolve().parents[2]


def run_python(backend, source):
    env = os.environ.copy()
    env["RPLY_BACKEND"] = backend
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-c", source], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30,
    )


BLOCK_EXTENSION = """
import importlib
original = importlib.import_module
def blocked(name, *args, **kwargs):
    if name == 'rply._core_rs':
        raise ImportError('extension deliberately unavailable')
    return original(name, *args, **kwargs)
importlib.import_module = blocked
"""


@pytest.mark.parametrize("mode", ["auto", "python"])
def test_missing_extension_uses_python(mode):
    result = run_python(mode, BLOCK_EXTENSION + r"""
from rply import Grammar, LexerEngine, LRParser, LRTable, Token
lexer = LexerEngine()
lexer.add_rule('INITIAL', 'NUMBER', r'\d+')
assert [t.value for t in lexer.tokenize('42')] == ['42']
grammar = Grammar({'NUMBER'})
grammar.add_production('expr', ['NUMBER'], func=lambda p: p[1])
grammar.set_start('expr')
table = LRTable(grammar)
parser = LRParser(table)
assert lexer.backend == table.backend == parser.backend == 'python'
assert parser.parse([Token('NUMBER', 42)]) == 42
""")
    assert result.returncode == 0, result.stderr


def test_explicit_python_does_not_import_native_extension():
    result = run_python("python", BLOCK_EXTENSION.replace(
        "raise ImportError('extension deliberately unavailable')",
        "raise AssertionError('Python mode attempted to load Rust')",
    ) + "from rply import LexerEngine; assert LexerEngine().backend == 'python'")
    assert result.returncode == 0, result.stderr


def test_explicit_rust_requires_extension():
    result = run_python("rust", BLOCK_EXTENSION + "import rply")
    assert result.returncode != 0
    assert "RPLY_BACKEND=rust requires" in result.stderr


def test_invalid_backend_is_rejected():
    result = run_python("unknown", "import rply")
    assert result.returncode != 0
    assert "RPLY_BACKEND must be" in result.stderr


@pytest.mark.parametrize("mode", ["auto", "rust"])
def test_native_setup_failure_obeys_policy(monkeypatch, mode):
    def broken():
        raise ValueError("native setup failed")

    monkeypatch.setattr(backends, "BACKEND", mode)
    monkeypatch.setattr(lexer_module, "HAS_RUST_CORE", True)
    monkeypatch.setattr(lexer_module, "native_core", SimpleNamespace(LexerEngine=broken))
    if mode == "rust":
        with pytest.raises(RuntimeError, match="Rust lexer initialization failed"):
            LexerEngine()
    else:
        with pytest.warns(RuntimeWarning, match="native setup failed"):
            lexer = LexerEngine()
        lexer.add_rule("INITIAL", "NUMBER", r"\d+")
        assert lexer.backend == "python"
        assert next(lexer.tokenize("42")).value == "42"


def test_runtime_action_error_is_not_replayed():
    calls = []

    def fail(proxy):
        calls.append(proxy[1])
        raise ValueError("action failure")

    grammar = Grammar({"NUMBER"})
    grammar.add_production("expr", ["NUMBER"], func=fail)
    grammar.set_start("expr")
    parser = LRParser(LRTable(grammar))
    with pytest.raises(ValueError, match="action failure"):
        parser.parse([Token("NUMBER", 42)])
    assert calls == [42]


@pytest.mark.parametrize("mode", ["auto", "rust"])
def test_unsupported_native_regex_flag_obeys_policy(mode):
    if importlib.util.find_spec("rply._core_rs") is None:
        pytest.skip("native extension unavailable")
    source = r'''
import re
import warnings
from rply import LexerEngine
engine = LexerEngine()
assert engine.backend == 'rust'
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always')
    engine.add_rule('INITIAL', 'WORD', r'\w+', flags=re.ASCII)
assert engine.backend == 'python'
assert any('Unsupported Python regex flags' in str(w.message) for w in caught)
assert next(engine.tokenize('abc')).value == 'abc'
'''
    result = run_python(mode, source)
    if mode == "auto":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "Unsupported Python regex flags" in result.stderr
