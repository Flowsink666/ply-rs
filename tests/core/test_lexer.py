"""Core lexer regression tests."""

import gc
import weakref

import pytest

from rply import LexerEngine


def test_lexer_error_recovery_no_infinite_loop():
    """Verify that ignored tokens followed by illegal characters do not loop indefinitely."""
    engine = LexerEngine()
    engine.add_rule("INITIAL", "WHITESPACE", r"\s+", func=lambda tok: None)
    engine.set_error_func("INITIAL", lambda tok: None)
    engine.input("   ?")
    tok = engine.token()
    assert tok is None


def test_callback_positions_and_state_stack():
    engine = LexerEngine()
    engine.add_state("STRING", "exclusive")

    def open_string(tok):
        tok.lexer.push_state("STRING")
        return tok

    def close_string(tok):
        tok.lexer.pop_state()
        return tok

    engine.add_rule("INITIAL", "OPEN", r"\[", open_string)
    engine.add_rule("INITIAL", "NUMBER", r"\d+")
    engine.add_rule("STRING", "TEXT", r"[^\]]+")
    engine.add_rule("STRING", "CLOSE", r"\]", close_string)
    tokens = list(engine.tokenize("[hello]42"))
    assert [(t.type, t.value, t.lexpos) for t in tokens] == [
        ("OPEN", "[", 0), ("TEXT", "hello", 1),
        ("CLOSE", "]", 6), ("NUMBER", "42", 7),
    ]
    assert engine.current_state == "INITIAL"
    assert engine.state_stack == []


def test_callback_skip_and_line_updates():
    engine = LexerEngine()

    def newline(tok):
        tok.lexer.lineno += 1

    engine.add_rule("INITIAL", "NEWLINE", r"\n", newline)
    engine.add_rule("INITIAL", "NUMBER", r"\d+")
    engine.set_error_func("INITIAL", lambda tok: tok.lexer.skip(1))
    tokens = list(engine.tokenize("\n?42"))
    assert [(t.value, t.lineno, t.lexpos) for t in tokens] == [("42", 2, 2)]


@pytest.mark.parametrize("with_callback", [False, True])
def test_lexer_reference_cycles_are_collectible(with_callback):
    engine = LexerEngine()
    if with_callback:
        def callback(tok, owner=engine):
            owner.lineno += 1
            return tok
        engine.add_rule("INITIAL", "NUMBER", r"\d+", callback)
        del callback
    reference = weakref.ref(engine)
    del engine
    gc.collect()
    assert reference() is None
