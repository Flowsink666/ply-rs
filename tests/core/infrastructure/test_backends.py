"""Backend selection, strict native mode, and observable setup failures."""

from types import SimpleNamespace

import ply_rs.core.lexer_engine as lexer_module
import pytest

from ply_rs import LexerEngine

from tests.fixtures.process import run_python

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


@pytest.mark.parametrize("mode", ["auto", "rust"])
def test_native_extension_is_required_in_both_modes(mode):
    result = run_python(mode, BLOCK_EXTENSION + "import ply_rs")
    assert result.returncode != 0
    assert "ply-rs requires the ply_rs._core_rs native extension" in result.stderr


def test_native_setup_failure_propagates_without_downgrade(monkeypatch):
    failure = ValueError("native setup failed")
    def broken():
        raise failure
    monkeypatch.setattr(lexer_module, "native_core", SimpleNamespace(LexerEngine=broken))
    with pytest.raises(ValueError) as caught:
        LexerEngine()
    assert caught.value is failure


@pytest.mark.parametrize("mode", ["auto", "rust"])
def test_backend_aliases_select_native_components_with_compatible_regex(mode):
    result = run_python(mode, r'''
import re
import warnings
from ply_rs import Grammar, LexerEngine, LRParser, LRTable
engine = LexerEngine()
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always')
    engine.add_rule('INITIAL', 'PAIR', r'(a)\1')
    engine.add_rule('INITIAL', 'WORD', r'\w+', flags=re.ASCII)
assert engine.backend == 'rust' and not caught
assert next(engine.tokenize('aa')).type == 'PAIR'
assert next(engine.tokenize('abc')).type == 'WORD'
grammar = Grammar({'WORD'})
grammar.add_production('s', ['WORD'])
grammar.set_start('s')
table = LRTable(grammar)
parser = LRParser(table)
assert table.backend == parser.backend == 'rust'
assert parser.parse(engine.tokenize('abc')) == 'abc'
''')
    assert result.returncode == 0, result.stderr
