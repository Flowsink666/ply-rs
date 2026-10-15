"""Lexer input, snapshots, counters, and callback reentry."""

from dataclasses import replace
import sys

import pytest

from ply_rs import LexerEngine
from ply_rs.backends import native_core


def make_lexer():
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "WORD", r"\w+")
    return lexer


def test_ignored_action_changes_position_and_state_immediately():
    engine = LexerEngine()
    engine.add_state("OTHER", "exclusive")

    def switch(tok):
        tok.lexer.skip(1)
        tok.lexer.begin("OTHER")
        tok.lexer.lineno = 7

    engine.add_rule("INITIAL", "SWITCH", "a", switch)
    engine.add_rule("OTHER", "B", "b")
    tokens = list(engine.tokenize("a?b"))
    assert [(t.type, t.lexpos, t.lineno) for t in tokens] == [("B", 2, 7)]


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


def test_ignored_callback_reentry_refreshes_ignore_and_restores_scan_state():
    lexer = LexerEngine()
    lexer.add_state("inner", "exclusive")
    lexer.add_rule("inner", "INNER", "x")
    lexer.add_rule("INITIAL", "WORD", "[a-z]+")
    trace = []

    def change(token):
        token.lexer.set_ignore("INITIAL", "#\n")
        token.lexer.begin("inner")
        trace.append((token.lexer.token().type, token.lexer.pos))
        token.lexer.begin("INITIAL")

    lexer.add_rule("INITIAL", "CHANGE", "!", change)
    assert [(t.type, t.value, t.lineno, t.lexpos) for t in lexer.tokenize("a!x#\nb")] == [
        ("WORD", "a", 1, 0), ("WORD", "b", 2, 5),
    ]
    assert trace == [("INNER", 3)]


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


def test_direct_native_counters_reject_overflow_without_state_changes():
    from ply_rs.core.lexer_config import MAX_LEXER_COUNTER

    lexer = native_core.LexerEngine()
    lexer.pos = MAX_LEXER_COUNTER
    with pytest.raises(OverflowError):
        lexer.skip()
    assert lexer.pos == MAX_LEXER_COUNTER
    with pytest.raises(OverflowError):
        native_core.Token("CHAR", "a", lexpos=MAX_LEXER_COUNTER)
    assert native_core.Token("CHAR", "a", lexpos=MAX_LEXER_COUNTER, end=0).end == 0


@pytest.mark.parametrize("direct", [False, True])
def test_newline_counter_overflow_preserves_scan_position(direct):
    from ply_rs.core.lexer_config import MAX_LEXER_COUNTER

    lexer = native_core.LexerEngine() if direct else LexerEngine()
    lexer.set_ignore("INITIAL", "\n")
    lexer.input("\n")
    lexer.lineno = MAX_LEXER_COUNTER
    with pytest.raises(OverflowError):
        lexer.token()
    assert lexer.pos == 0
    assert lexer.lineno == MAX_LEXER_COUNTER


@pytest.mark.parametrize("factory,nested", [
    pytest.param(LexerEngine, False, id="public-empty-stack"),
    pytest.param(native_core.LexerEngine, False, id="native-empty-stack"),
    pytest.param(LexerEngine, True, id="public-nested-stack"),
])
def test_invalid_push_preserves_state_stack(factory, nested):
    lexer = factory()
    if nested:
        lexer.add_state("other")
        lexer.push_state("other")
    with pytest.raises(ValueError, match="Undefined lexer state"):
        lexer.push_state("missing")
    assert lexer.current_state == ("other" if nested else "INITIAL")
    assert lexer.state_stack == (["INITIAL"] if nested else [])
