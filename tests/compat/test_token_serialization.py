"""Public token copy, pickle and subclass state contracts."""

import copy
import pickle

import pytest

from ply_rs.compat.ply.lex import LexToken
from ply_rs.compat.sly.lex import Token
from tests.fixtures.compat import TrackedText, make_lexer, scan, token_position


def pickle_roundtrip(token):
    return pickle.loads(pickle.dumps(token))


@pytest.mark.parametrize("token_type", [LexToken, Token], ids=["ply", "sly"])
@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle_roundtrip])
def test_public_token_fields_and_mutable_values_survive_serialization(token_type, operation):
    tok = token_type()
    tok.type, tok.value, tok.lineno = "WORD", ["saved"], 7
    if token_type is LexToken:
        tok.lexpos, tok.lexer = 17, None
    else:
        tok.index, tok.end = 17, 22
    clone = operation(tok)
    assert type(clone) is token_type and clone is not tok
    assert (clone.type, clone.value, clone.lineno) == ("WORD", ["saved"], 7)
    assert (clone.value is tok.value) == (operation is copy.copy)
    if token_type is LexToken:
        assert clone.lexpos == 17 and clone.lexer is None
    else:
        assert (clone.index, clone.end) == (17, 22)
    del tok.value
    clone = operation(tok)
    with pytest.raises(AttributeError, match="has no attribute 'value'"):
        _ = clone.value


@pytest.mark.parametrize("adapter,operation", [
    pytest.param("ply", copy.copy, id="ply-copy"),
    pytest.param("sly", pickle_roundtrip, id="sly-pickle"),
])
def test_unread_error_token_serializes_public_value(adapter, operation):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    source = TrackedText("？旧")
    assert scan(adapter, lexer, source) == []
    tok = saved[0]
    if adapter == "ply":
        tok.lexer = None
    assert source.slices == []
    clone = operation(tok)
    assert type(clone) is type(tok) and clone is not tok
    assert clone.value == tok.value == "？旧"
    assert token_position(adapter, clone) == 0
    assert source.slices == [slice(0, None)]


class ExtendedPlyToken(LexToken):
    __slots__ = ("extra", "_value", "__private", "__dict__")


class ExtendedSlyToken(Token):
    __slots__ = ("extra", "_value", "__private", "__dict__")


@pytest.mark.parametrize("token_class,operation", [
    pytest.param(ExtendedPlyToken, copy.copy, id="subclass-shallow-copy"),
    pytest.param(ExtendedPlyToken, copy.deepcopy, id="subclass-deep-copy"),
    pytest.param(ExtendedSlyToken, pickle_roundtrip, id="subclass-pickle"),
])
def test_subclass_slots_dictionary_and_value_metadata_survive(token_class, operation):
    tok = token_class()
    tok.value, tok._value = ["payload"], ["metadata"]
    tok.extra, tok.details = ["slot"], ["dictionary"]
    private_name = f"_{token_class.__name__}__private"
    setattr(tok, private_name, ["private"])
    clone = operation(tok)
    assert type(clone) is token_class
    assert clone.value == ["payload"] and clone._value == ["metadata"]
    assert clone.extra == ["slot"] and clone.details == ["dictionary"]
    assert getattr(clone, private_name) == ["private"]
    for name in ("value", "_value", "extra", "details", private_name):
        assert (getattr(clone, name) is getattr(tok, name)) == (operation is copy.copy)
