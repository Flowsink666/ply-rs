"""Backend selection, strict native mode, and observable setup failures."""

import os
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import ply_rs.backends as backends
import ply_rs.core.lexer_engine as lexer_module
from ply_rs import Grammar, LexerEngine, LRParser, LRTable, Token

ROOT = Path(__file__).resolve().parents[2]


def run_python(backend, source):
    env = os.environ.copy()
    env["PLY_RS_BACKEND"] = backend
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-c", source], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30,
    )


BLOCK_EXTENSION = """
import importlib
original = importlib.import_module
def blocked(name, *args, **kwargs):
    if name == 'ply_rs._core_rs':
        raise ImportError('extension deliberately unavailable')
    return original(name, *args, **kwargs)
importlib.import_module = blocked
"""








def test_invalid_backend_is_rejected():
    result = run_python("unknown", "import ply_rs")
    assert result.returncode != 0
    assert "PLY_RS_BACKEND must be" in result.stderr




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
def test_native_extension_is_required_in_both_modes(mode):
    result = run_python(mode, BLOCK_EXTENSION + "import ply_rs")
    assert result.returncode != 0
    assert "ply-rs requires the ply_rs._core_rs native extension" in result.stderr


def test_removed_python_backend_is_rejected_before_extension_loading():
    result = run_python("python", BLOCK_EXTENSION + "import ply_rs")
    assert result.returncode != 0
    assert "PLY_RS_BACKEND=python is no longer supported" in result.stderr


def test_native_setup_failure_propagates_without_downgrade(monkeypatch):
    failure = ValueError("native setup failed")
    def broken():
        raise failure
    monkeypatch.setattr(lexer_module, "native_core", SimpleNamespace(LexerEngine=broken))
    with pytest.raises(ValueError) as caught:
        LexerEngine()
    assert caught.value is failure


@pytest.mark.parametrize("mode", ["auto", "rust"])
def test_regex_compatibility_does_not_change_selected_backend(mode):
    result = run_python(mode, r'''
import re
import warnings
from ply_rs import LexerEngine
engine = LexerEngine()
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always')
    engine.add_rule('INITIAL', 'PAIR', r'(a)\1')
    engine.add_rule('INITIAL', 'WORD', r'\w+', flags=re.ASCII)
assert engine.backend == 'rust' and not caught
assert next(engine.tokenize('aa')).type == 'PAIR'
assert next(engine.tokenize('abc')).type == 'WORD'
''')
    assert result.returncode == 0, result.stderr
