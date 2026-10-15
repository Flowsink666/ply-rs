"""Rule registration and configuration changes preserve active scanning."""

import re
import warnings

import pytest

from ply_rs import LexError, LexerEngine
from ply_rs.backends import native_core


def make_lexer():
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "WORD", r"\w+")
    return lexer


def test_registering_initial_rule_updates_inclusive_state():
    engine = LexerEngine()
    engine.add_state("OTHER", "inclusive")
    engine.add_rule("INITIAL", "A", "a")
    engine.input("ab")
    engine.begin("OTHER")
    assert engine.token().type == "A"
    engine.add_rule("INITIAL", "B", "b")
    assert engine.token().type == "B"


def test_lexer_priority_is_stable_across_inclusive_states():
    lexer = LexerEngine()
    lexer.add_state("other", "inclusive")
    lexer.add_rule("INITIAL", "SHORT", "a")
    lexer.add_rule("INITIAL", "FIRST", "ab", priority=10)
    lexer.add_rule("INITIAL", "SECOND", "ab", priority=10)
    lexer.add_rule("other", "LOCAL", "ab", priority=20)
    lexer.input("ab")
    assert lexer.token().type == "FIRST"
    lexer.input("ab")
    lexer.begin("other")
    assert lexer.token().type == "LOCAL"
    # Registering a higher priority rule invalidates active rule caches.
    lexer.add_rule("INITIAL", "NEW", "ab", priority=30)
    lexer.input("ab")
    lexer.begin("other")
    assert lexer.token().type == "NEW"


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


@pytest.mark.parametrize("from_error", [False, True])
def test_rule_registration_after_callback_finishes_without_replay(from_error):
    lexer = LexerEngine()
    calls = []

    def configure(tok):
        calls.append("before")
        tok.lexer.add_rule("INITIAL", "WORD", r"\w+", flags=re.ASCII)
        tok.lexer.lineno = 9
        calls.append("after")

    if from_error:
        lexer.set_error_func("INITIAL", configure)
    else:
        lexer.add_rule("INITIAL", "SETUP", "@", configure)
    tokens = list(lexer.tokenize("@abc"))
    assert [(t.type, t.value, t.lexpos, t.lineno) for t in tokens] == [("WORD", "abc", 1, 9)]
    assert calls == ["before", "after"]


def test_rule_registration_preserves_nested_state_and_callback_owner():
    lexer = LexerEngine()
    lexer.add_state("inside", "exclusive")
    lexer.add_rule("inside", "N", r"\d+")
    lexer.set_ignore("inside", " ")
    calls = []

    def word(tok):
        assert tok.lexer is lexer
        calls.append(tok.value)
        tok.lexer.pop_state()
        return tok

    lexer.input("1 abc")
    lexer.push_state("inside")
    assert lexer.token().value == "1"
    lexer.lineno = 7
    stack = lexer.state_stack
    lexer.add_rule("inside", "WORD", r"\w+", word, flags=re.ASCII)
    assert lexer.current_state == "inside" and stack == ["INITIAL"]
    tok = lexer.token()
    assert (tok.type, tok.value, tok.lineno, tok.lexpos) == ("WORD", "abc", 7, 2)
    assert calls == ["abc"]
    assert lexer.current_state == "INITIAL" and stack == []


def test_ignored_callback_refreshes_grouped_rules_and_committed_input():
    lexer = LexerEngine()
    calls = []

    def replace_input(token):
        calls.append(token.value)
        token.lexer.add_rule("INITIAL", "NEW", "ab", priority=10)
        token.lexer.input("ab")

    lexer.add_rule("INITIAL", "REPLACE", "!", replace_input)
    lexer.add_rule("INITIAL", "A", "a")
    lexer.add_rule("INITIAL", "B", "b")
    tokens = list(lexer.tokenize("!old"))
    assert [(token.type, token.value) for token in tokens] == [("NEW", "ab")]
    assert calls == ["!"]
    assert lexer.data == "ab"
    assert lexer.pos == 2


def test_warm_inclusive_scanner_observes_new_initial_rules_and_state_kind():
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "A", "a")
    lexer.add_state("child", "inclusive")
    lexer.add_rule("child", "B", "b")
    lexer.input("aba")
    lexer.begin("child")
    assert lexer.token().type == "A"
    lexer.add_rule("INITIAL", "PAIR", "ba", priority=10)
    assert lexer.token().type == "PAIR"
    lexer.input("ab")
    lexer.begin("child")
    assert lexer.token().type == "A"
    lexer.add_state("child", "exclusive")
    assert lexer.token().type == "B"
    lexer.input("a")
    lexer.begin("child")
    with pytest.raises(LexError):
        lexer.token()


@pytest.mark.parametrize("flags_failure", [False, True])
def test_rejected_configuration_preserves_definitions_and_scan_state(flags_failure):
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "N", r"\d+")
    lexer.input("12")
    before = lexer.capture_state()
    with pytest.raises((re.error, ValueError)):
        if flags_failure:
            lexer.add_rule("new", "BAD", "a", flags=re.ASCII | re.UNICODE)
        else:
            lexer.add_rule("new", "BAD", "(")
    assert lexer.capture_state() == before
    assert "new" not in lexer.states and "new" not in lexer.rules
    with pytest.raises(ValueError, match="Undefined lexer state"):
        lexer.begin("new")
    assert lexer.token().value == "12"


@pytest.mark.parametrize("from_error", [False, True])
def test_input_replaced_after_callback_registration_is_not_skipped(from_error):
    lexer = LexerEngine()
    calls = []

    def action(tok):
        calls.append(tok.value)
        tok.lexer.add_rule("INITIAL", "WORD", r"\w+", flags=re.ASCII)
        tok.lexer.input("abc")

    if from_error:
        lexer.set_error_func("INITIAL", action)
    else:
        lexer.add_rule("INITIAL", "SETUP", "@", action)
    assert [(t.value, t.lexpos) for t in lexer.tokenize("@12")] == [("abc", 0)]
    assert len(calls) == 1


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


def test_failed_native_setup_preserves_runtime_and_definitions(monkeypatch):
    lexer = make_lexer()
    lexer.input("12")
    before = lexer.capture_state()
    runtime = lexer._runtime
    revision = lexer._config.revision
    failure = ValueError("replacement construction failed")

    def broken(*args, **kwargs):
        raise failure

    monkeypatch.setattr(lexer._runtime, "configure", broken)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(ValueError) as caught:
            lexer.add_rule("new", "BAD", r"(?=a)a")
    assert caught.value is failure
    assert lexer.backend == "rust" and lexer._runtime is runtime
    assert lexer.capture_state() == before
    assert lexer._config.revision == revision and "new" not in lexer.states
    assert lexer.token().value == "12"


@pytest.mark.parametrize("direct", [False, True])
def test_rejected_rule_preserves_state_and_original_exception(direct):
    lexer = native_core.LexerEngine() if direct else LexerEngine()
    lexer.add_rule("INITIAL", "A", "a")
    lexer.input("a")
    with pytest.raises(re.error):
        lexer.add_rule("missing", "BAD", "(")
    with pytest.raises(ValueError, match="Undefined lexer state"):
        lexer.begin("missing")
    assert lexer.token().value == "a"


def test_direct_native_literal_configuration_is_atomic_and_reentrant():
    lexer = native_core.LexerEngine()
    lexer.set_literals("x")

    class Literals:
        def __iter__(self):
            lexer.input("x")
            assert lexer.token().value == "x"
            return iter(["+"])

    lexer.set_literals(Literals())
    with pytest.raises(TypeError):
        lexer.set_literals(["-", 1])
    lexer.input("+")
    assert lexer.token().value == "+"
