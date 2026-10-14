"""State ownership, explicit contexts, and backend-independent public behavior."""

import importlib.util
from types import SimpleNamespace

import pytest

from rply import Grammar, LexerEngine, LexError, LRParser, LRTable, Token
from tests.core.test_backends import run_python


def test_lexer_snapshot_restores_independent_runtime_state():
    lexer = LexerEngine()
    lexer.add_state("inside", "exclusive")
    lexer.add_rule("inside", "N", r"\d+")
    lexer.set_ignore("inside", " ")
    lexer.input("1 2")
    lexer.push_state("inside")
    assert lexer.token().value == "1"
    lexer.lineno = 9
    snapshot = lexer.capture_state()
    stack_view = lexer.state_stack
    lexer.input("99")
    assert stack_view == []
    lexer.restore_state(snapshot)
    assert lexer.token().value == "2"
    assert (lexer.lineno, lexer.current_state, list(stack_view)) == (9, "inside", ["INITIAL"])
    stack_view[:] = ["inside", "INITIAL"]
    lexer.pop_state()
    assert lexer.current_state == "INITIAL"
    assert stack_view.copy() == ["inside"]
    lexer.restore_state(snapshot)
    assert lexer.token().value == "2"


def test_ignored_callback_can_register_rule_for_the_same_token_call():
    lexer = LexerEngine()
    calls = []

    def configure(tok):
        assert tok.lexer is lexer
        calls.append(tok.value)
        tok.lexer.add_rule("INITIAL", "N", r"\d+")

    lexer.add_rule("INITIAL", "SETUP", "@", configure)
    assert [(t.type, t.value) for t in lexer.tokenize("@12")] == [("N", "12")]
    assert calls == ["@"]


def test_existing_state_configuration_can_change_kind():
    lexer = LexerEngine()
    lexer.add_state("other", "inclusive")
    lexer.add_rule("INITIAL", "N", r"\d+")
    lexer.input("12")
    lexer.begin("other")
    assert lexer.token().value == "12"
    lexer.add_state("other", "exclusive")
    lexer.input("12")
    lexer.begin("other")
    with pytest.raises(LexError):
        lexer.token()


def test_failed_push_does_not_modify_state_stack():
    lexer = LexerEngine()
    lexer.add_state("other")
    lexer.push_state("other")
    with pytest.raises(ValueError):
        lexer.push_state("missing")
    assert lexer.current_state == "other"
    assert lexer.state_stack == ["INITIAL"]


def test_parse_context_survives_recursive_parse_and_does_not_leak():
    grammar = Grammar({"N"})

    def action(p):
        if p[1] == 2:
            inner = parser.parse([Token("N", 1)], lexer=inner_lexer, parser=inner_owner)
            return p.parser.name, p.lexer.name, inner
        return p.parser.name, p.lexer.name if p.lexer is not None else None

    grammar.add_production("s", ["N"], action)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    parser.name = "default"
    outer_owner = SimpleNamespace(name="outer")
    inner_owner = SimpleNamespace(name="inner")
    outer_lexer = SimpleNamespace(name="outer-lexer")
    inner_lexer = SimpleNamespace(name="inner-lexer")
    assert parser.parse([Token("N", 2)], lexer=outer_lexer, parser=outer_owner) == (
        "outer", "outer-lexer", ("inner", "inner-lexer"),
    )
    assert parser.parse([Token("N", 1)]) == ("default", None)


def test_error_callback_updates_apply_to_the_next_parse():
    grammar = Grammar({"N", "BAD"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar), error_func=lambda tok: "first")
    assert parser.parse([Token("BAD", "?")]) == "first"
    parser.error_func = lambda tok: "second"
    assert parser.parse([Token("BAD", "?")]) == "second"


def test_replacing_public_action_dictionary_applies_to_new_parser():
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    table = LRTable(grammar)
    assert LRParser(table).parse([Token("N", 1)]) == 1
    replacement = {state: row.copy() for state, row in table.action.items()}
    replacement[0]["N"] = None
    table.action = replacement
    with pytest.raises(SyntaxError):
        LRParser(table).parse([Token("N", 1)])


def test_parser_accepts_table_data_without_builder_diagnostics():
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    table = SimpleNamespace(
        productions=grammar.Productions,
        action={0: {"N": 1}, 1: {"$end": -1}, 2: {"$end": 0}},
        goto={0: {"s": 2}},
    )
    assert LRParser(table).parse([Token("N", 42)]) == 42


@pytest.mark.parametrize("from_error", [False, True])
def test_rule_registration_after_callback_finishes_without_replay(from_error):
    if importlib.util.find_spec("rply._core_rs") is None:
        pytest.skip("native extension unavailable")
    source = r'''
import re
import warnings
from rply import LexerEngine
lexer = LexerEngine()
assert lexer.backend == 'rust'
calls = []
def configure(tok):
    calls.append('before')
    tok.lexer.add_rule('INITIAL', 'WORD', r'\w+', flags=re.ASCII)
    tok.lexer.lineno = 9
    calls.append('after')
if FROM_ERROR:
    lexer.set_error_func('INITIAL', configure)
else:
    lexer.add_rule('INITIAL', 'SETUP', '@', configure)
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always')
    result = list(lexer.tokenize('@abc'))
assert lexer.backend == 'rust'
assert [(t.type, t.value, t.lexpos, t.lineno) for t in result] == [('WORD', 'abc', 1, 9)]
assert calls == ['before', 'after']
assert len(caught) == 0
'''.replace("FROM_ERROR", str(from_error))
    result = run_python("auto", source)
    assert result.returncode == 0, result.stderr


def test_rule_registration_preserves_nested_state_and_callback_owner():
    if importlib.util.find_spec("rply._core_rs") is None:
        pytest.skip("native extension unavailable")
    result = run_python("auto", r'''
import re
import warnings
from rply import LexerEngine
lexer = LexerEngine()
assert lexer.backend == 'rust'
lexer.add_state('inside', 'exclusive')
lexer.add_rule('inside', 'N', r'\d+')
lexer.set_ignore('inside', ' ')
calls = []
def word(tok):
    assert tok.lexer is lexer
    calls.append(tok.value)
    tok.lexer.pop_state()
    return tok
lexer.input('1 abc')
lexer.push_state('inside')
assert lexer.token().value == '1'
lexer.lineno = 7
stack = lexer.state_stack
with warnings.catch_warnings(record=True):
    warnings.simplefilter('always')
    lexer.add_rule('inside', 'WORD', r'\w+', word, flags=re.ASCII)
assert lexer.backend == 'rust'
assert lexer.current_state == 'inside' and stack == ['INITIAL']
tok = lexer.token()
assert (tok.type, tok.value, tok.lineno, tok.lexpos) == ('WORD', 'abc', 7, 2)
assert calls == ['abc']
assert lexer.current_state == 'INITIAL' and stack == []
''')
    assert result.returncode == 0, result.stderr


def test_native_engines_do_not_import_removed_backend():
    if importlib.util.find_spec("rply._core_rs") is None:
        pytest.skip("native extension unavailable")
    result = run_python("rust", r'''
import importlib.abc
import sys
class BlockRemovedBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('rply.backends.python'):
            raise AssertionError('Removed backend imported')
sys.meta_path.insert(0, BlockRemovedBackend())
from rply import Grammar, LexerEngine, LRParser, LRTable
lexer = LexerEngine()
lexer.add_rule('INITIAL', 'N', r'\d+')
grammar = Grammar({'N'})
grammar.add_production('s', ['N'], lambda p: p[1])
grammar.set_start('s')
table = LRTable(grammar)
parser = LRParser(table)
assert lexer.backend == table.backend == parser.backend == 'rust'
assert parser.parse(lexer.tokenize('12')) == '12'
''')
    assert result.returncode == 0, result.stderr
