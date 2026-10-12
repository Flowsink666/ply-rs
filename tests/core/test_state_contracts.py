"""Runtime changes and failed operations obey the same public contract."""

from dataclasses import replace
import importlib.util
import sys
from types import SimpleNamespace
import warnings

import pytest

from rply import Grammar, LexerEngine, LexError, LRParser, LRTable, Token
from rply.core.production import ParserError
import rply.backends as backends
import rply.core.lexer_engine as lexer_module
from tests.core.test_backends import run_python


def make_lexer():
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "WORD", r"\w+")
    return lexer


def make_parser(function=None, style="proxy"):
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], function, action_style=style)
    grammar.set_start("s")
    return LRParser(LRTable(grammar))


@pytest.mark.parametrize("from_error", [False, True])
@pytest.mark.parametrize("use_snapshot", [False, True])
@pytest.mark.parametrize("new_text", ["99", "中文", ""])
def test_callback_replaces_input_without_replay_or_skipping(from_error, use_snapshot, new_text):
    lexer = make_lexer()
    replacement = make_lexer()
    replacement.input(new_text)
    calls = []

    def action(tok):
        calls.append(tok.value)
        assert len(calls) == 1
        if use_snapshot:
            tok.lexer.restore_state(replacement.capture_state())
        else:
            tok.lexer.input(new_text)

    if from_error:
        lexer.set_error_func("INITIAL", action)
    else:
        lexer.add_rule("INITIAL", "SETUP", "@", action)
    # Unicode in the original input exercises the native character-index borrow.
    assert [t.value for t in lexer.tokenize("@旧输入")] == ([new_text] if new_text else [])
    assert len(calls) == 1
    assert (lexer.data, lexer.pos, lexer.length) == (new_text, len(new_text), len(new_text))


def test_callback_can_return_a_token_after_replacing_input():
    lexer = make_lexer()

    def action(tok):
        tok.lexer.input("99")
        return tok

    lexer.add_rule("INITIAL", "SETUP", "@", action)
    assert [(t.type, t.value) for t in lexer.tokenize("@12")] == [("SETUP", "@"), ("WORD", "99")]


def test_callback_snapshot_restores_state_stack_and_position():
    lexer = make_lexer()
    lexer.add_state("inside", "exclusive")
    lexer.add_rule("inside", "WORD", r"\w+")
    lexer.input("x99")
    lexer.pos = 1
    lexer.lineno = 7
    lexer.push_state("inside")
    snapshot = lexer.capture_state()
    lexer.add_rule("INITIAL", "SETUP", "@", lambda tok: tok.lexer.restore_state(snapshot))
    assert [(t.value, t.lexpos, t.lineno) for t in lexer.tokenize("@旧")] == [("99", 1, 7)]
    assert lexer.current_state == "inside"
    assert lexer.state_stack == ["INITIAL"]


@pytest.mark.parametrize("changes", [
    {"pos": -1}, {"lineno": -1}, {"pos": "bad"}, {"lineno": "bad"},
    {"data": None}, {"current_state": "missing"}, {"state_stack": ("missing",)},
    {"pos": 2 * sys.maxsize + 2},
])
def test_invalid_snapshot_preserves_every_runtime_field(changes):
    lexer = make_lexer()
    lexer.add_state("inside")
    lexer.input("old")
    lexer.pos = 1
    lexer.lineno = 9
    lexer.push_state("inside")
    before = lexer.capture_state()
    revision = lexer._input_revision
    malformed = replace(before, **{"data": "new", **changes})
    with pytest.raises((ValueError, TypeError, OverflowError)):
        lexer.restore_state(malformed)
    assert lexer.capture_state() == before
    assert lexer._input_revision == revision


def test_snapshot_allows_position_past_eof_and_zero_line_number():
    lexer = make_lexer()
    lexer.input("x")
    snapshot = replace(lexer.capture_state(), pos=100, lineno=0)
    lexer.restore_state(snapshot)
    assert lexer.capture_state() == snapshot
    assert lexer.token() is None


def test_invalid_input_and_counter_edits_preserve_state():
    lexer = make_lexer()
    lexer.input("old")
    before = lexer.capture_state()
    with pytest.raises(TypeError):
        lexer.input(None)
    with pytest.raises(ValueError):
        lexer.pos = -1
    with pytest.raises(TypeError):
        lexer.lineno = "bad"
    assert lexer.capture_state() == before
    lexer.pos = 2
    lexer.skip(-1)
    assert lexer.token().value == "ld"


@pytest.mark.parametrize("initial,changed", [
    ("none", "proxy"), ("none", "values"), ("proxy", "proxy2"),
    ("values", "values2"), ("proxy", "none"), ("values", "none"),
    ("proxy", "values"), ("values", "proxy"),
])
def test_action_presence_style_and_function_updates_between_parses(initial, changed):
    actions = {
        "none": (None, "proxy", 1),
        "proxy": (lambda p: p[1] + 10, "proxy", 11),
        "proxy2": (lambda p: p[1] + 20, "proxy", 21),
        "values": (lambda value: value + 10, "values", 11),
        "values2": (lambda value: value + 20, "values", 21),
    }
    function, style, expected = actions[initial]
    parser = make_parser(function, style)
    assert parser.parse([Token("N", 1)]) == expected
    function, style, expected = actions[changed]
    parser.productions[1].func = function
    parser.productions[1].action_style = style
    assert parser.parse([Token("N", 1)]) == expected


@pytest.mark.parametrize("replace_dictionary", [False, True])
def test_public_parser_action_edits_apply_to_existing_instance(replace_dictionary):
    parser = make_parser()
    assert parser.parse([Token("N", 1)]) == 1
    if replace_dictionary:
        parser.action = {state: row.copy() for state, row in parser.action.items()}
    previous = parser.action[0]["N"]
    parser.action[0]["N"] = None
    with pytest.raises(SyntaxError):
        parser.parse([Token("N", 1)])
    parser.action[0]["N"] = previous
    assert parser.parse([Token("N", 2)]) == 2


@pytest.mark.parametrize("replace_dictionary", [False, True])
def test_public_parser_goto_edits_apply_to_existing_instance(replace_dictionary):
    parser = make_parser()
    assert parser.parse([Token("N", 1)]) == 1
    if replace_dictionary:
        parser.goto = {state: row.copy() for state, row in parser.goto.items()}
    previous = parser.goto[0].pop("s")
    with pytest.raises((ParserError, RuntimeError), match="No GOTO"):
        parser.parse([Token("N", 1)])
    parser.goto[0]["s"] = previous
    assert parser.parse([Token("N", 2)]) == 2


def test_replaced_productions_apply_to_existing_parser():
    parser = make_parser()
    replacement = make_parser(lambda value: value + 10, "values")
    parser.productions = replacement.productions
    assert parser.parse([Token("N", 1)]) == 11


def test_recursive_parse_can_refresh_metadata_without_changing_outer_engine():
    def outer(p):
        parser.productions[1].func = lambda value: value + 10
        parser.productions[1].action_style = "values"
        inner = parser.parse([Token("N", 2)])
        return p.parser.name, p[1], inner

    parser = make_parser(outer)
    parser.name = "outer"
    assert parser.parse([Token("N", 1)]) == ("outer", 1, 12)
    assert parser.parse([Token("N", 3)]) == 13


def test_native_metadata_refresh_reuses_an_edited_runtime_table(monkeypatch):
    parser = make_parser()
    if parser.backend != "rust":
        pytest.skip("native table sharing test")
    # Keep a valid edit that prevents reuse of the builder's original artifact.
    parser.action[0]["UNUSED"] = None
    assert parser.parse([Token("N", 1)]) == 1
    native_class = parser._runtime.module.LRParserEngine

    def unexpected(*args, **kwargs):
        raise AssertionError("metadata-only edit reconstructed native table rows")

    monkeypatch.setattr(parser._runtime.module, "LRParserEngine", SimpleNamespace(
        from_engine=native_class.from_engine, from_table=unexpected,
    ))
    parser.productions[1].func = lambda p: p[1] + 10
    assert parser.parse([Token("N", 1)]) == 11


@pytest.mark.parametrize("mode", ["rust", "auto"])
@pytest.mark.parametrize("flags_failure", [False, True])
def test_rejected_native_configuration_keeps_both_definitions_and_scan_state(mode, flags_failure):
    if importlib.util.find_spec("rply._core_rs") is None:
        pytest.skip("native extension unavailable")
    source = r'''
import re
import warnings
from rply import LexerEngine
lexer = LexerEngine()
lexer.add_rule('INITIAL', 'N', r'\d+')
lexer.input('12')
before = lexer.capture_state()
revision = lexer._config.revision
with warnings.catch_warnings():
    warnings.simplefilter('error', RuntimeWarning)
    try:
        if FLAGS_FAILURE:
            lexer.add_rule('new', 'BAD', 'a', flags=re.ASCII)
        else:
            lexer.add_rule('new', 'BAD', r'(?=a)a')
    except ERROR_TYPE:
        pass
    else:
        raise AssertionError('registration should have failed')
assert lexer.backend == 'rust'
assert lexer.capture_state() == before
assert lexer._config.revision == revision
assert 'new' not in lexer.states and 'new' not in lexer.rules
try:
    lexer._runtime.engine.begin('new')
except ValueError:
    pass
else:
    raise AssertionError('native configuration was partially committed')
assert lexer.token().value == '12'
'''.replace("FLAGS_FAILURE", str(flags_failure)).replace(
        "ERROR_TYPE", "RuntimeError" if mode == "rust" else "RuntimeWarning")
    result = run_python(mode, source)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("from_error", [False, True])
def test_input_replaced_after_callback_fallback_is_not_skipped(from_error):
    if importlib.util.find_spec("rply._core_rs") is None:
        pytest.skip("native extension unavailable")
    source = r'''
import re
import warnings
from rply import LexerEngine
lexer = LexerEngine()
calls = []
def action(tok):
    calls.append(tok.value)
    tok.lexer.add_rule('INITIAL', 'WORD', r'\w+', flags=re.ASCII)
    tok.lexer.input('abc')
if FROM_ERROR:
    lexer.set_error_func('INITIAL', action)
else:
    lexer.add_rule('INITIAL', 'SETUP', '@', action)
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always')
    result = list(lexer.tokenize('@12'))
assert [(t.value, t.lexpos) for t in result] == [('abc', 0)]
assert lexer.backend == 'python' and len(calls) == 1 and len(caught) == 1
'''.replace("FROM_ERROR", str(from_error))
    result = run_python("auto", source)
    assert result.returncode == 0, result.stderr


def test_clearing_error_handler_and_rejected_literals_leave_valid_configuration():
    lexer = make_lexer()
    lexer.set_error_func("INITIAL", lambda tok: tok.lexer.skip())
    lexer.set_error_func("INITIAL", None)
    lexer.set_literals("+")
    with pytest.raises(ValueError):
        lexer.set_literals(["+", "two"])
    assert lexer.literals == {"+"}
    assert [(t.type, t.value) for t in lexer.tokenize("+")] == [("+", "+")]
    lexer.input("?")
    with pytest.raises(LexError):
        lexer.token()


def test_failed_handoff_preparation_preserves_selected_runtime_and_definitions(monkeypatch):
    lexer = make_lexer()
    if lexer.backend != "rust":
        pytest.skip("native handoff preparation test")
    lexer.input("12")
    before = lexer.capture_state()
    runtime = lexer._runtime
    revision = lexer._config.revision
    failure = ValueError("replacement construction failed")

    def broken(*args, **kwargs):
        raise failure

    monkeypatch.setattr(backends, "BACKEND", "auto")
    monkeypatch.setattr(lexer_module, "PythonLexerEngine", broken)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(ValueError) as caught:
            lexer.add_rule("new", "BAD", r"(?=a)a")
    assert caught.value is failure
    assert lexer.backend == "rust" and lexer._runtime is runtime
    assert lexer.capture_state() == before
    assert lexer._config.revision == revision and "new" not in lexer.states
    assert lexer.token().value == "12"
