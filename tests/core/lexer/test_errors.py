"""Lexical errors report public type, position, line and source suffix."""

import pytest

from ply_rs import LexerEngine, LexError


@pytest.mark.parametrize("empty_rule", [False, True], ids=["illegal-character", "empty-match"])
def test_lexical_error_preserves_source_location(empty_rule):
    lexer = LexerEngine()
    if empty_rule:
        lexer.add_rule("INITIAL", "EMPTY", r"")
    lexer.input("a？旧")
    lexer.pos, lexer.lineno = 1, 7
    with pytest.raises(LexError) as caught:
        lexer.token()
    error = caught.value
    assert type(error) is LexError
    assert (error.error_index, error.text) == (1, "？旧")
    if empty_rule:
        assert str(error) == 'Rule "EMPTY" matched an empty string at pos 1'
    else:
        assert str(error) == "Illegal character '？' at line 7, pos 1"
