"""Smoke contracts for forwarding deferred error values through PLY and SLY."""

import pytest

from ply_rs.compat.ply.lex import LexToken
from ply_rs.compat.sly.lex import Token
from tests.fixtures.compat import TrackedText, make_lexer, scan, token_position


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_saved_error_forwards_public_fields_and_original_suffix(adapter):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    source = TrackedText("ab？旧")
    assert [tok.value for tok in scan(adapter, lexer, source)] == ["ab"]
    tok = saved[0]
    assert type(tok) is (LexToken if adapter == "ply" else Token)
    assert token_position(adapter, tok) == 2
    assert source.slices == []
    tok.lineno = 7
    if adapter == "ply":
        assert tok.lexer is lexer
        tok.lexpos = 17
    else:
        assert tok.end == 4
        tok.index, tok.end = 17, 99
    assert [tok.value for tok in scan(adapter, lexer, "replacement")] == ["replacement"]
    assert tok.value == tok.value == "？旧"
    assert (tok.lineno, token_position(adapter, tok)) == (7, 17)
    assert source.slices == [slice(2, None)]
    if adapter == "sly":
        assert tok.end == 99


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_unread_value_can_be_assigned_deleted_and_restored(adapter):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    source = TrackedText("？？")
    assert scan(adapter, lexer, source) == []
    first, second = saved
    first.value = None
    assert first.value is None
    del second.value
    with pytest.raises(AttributeError, match="has no attribute 'value'"):
        _ = second.value
    replacement = []
    second.value = replacement
    assert second.value is replacement
    assert [token_position(adapter, tok) for tok in saved] == [0, 1]
    assert source.slices == []


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_returned_error_token_defers_read_and_keeps_slice_callback_assignment(adapter):
    def recover(tok, lexer):
        if adapter == "ply":
            lexer.skip()
        else:
            lexer.index += 1
        return tok

    lexer = make_lexer(adapter, recover)
    source = TrackedText("？")
    emitted = scan(adapter, lexer, source)
    assert len(emitted) == 1
    tok = emitted[0]
    assert source.slices == []
    source.on_slice = lambda: setattr(tok, "value", "edited")
    assert tok.value == tok.value == "edited"
    assert source.slices == [slice(0, None)]
