"""Copying and serializing compatibility tokens expose their public fields."""

import copy
import importlib
import pickle

import pytest

from ply_rs.compat.ply.lex import LexToken
from ply_rs.compat.sly.lex import Token
from tests.compat.test_lazy_errors import TrackedText, make_lexer, scan, token_position


@pytest.mark.parametrize("adapter", ["ply", "sly"])
@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_unread_error_token_can_be_copied_or_serialized_without_hidden_lexer_state(adapter, operation):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    source = TrackedText("？？")
    assert scan(adapter, lexer, source) == []
    tok = saved[0]
    assert source.slices == []

    # PLY exposes an explicit owner. Detaching it has always made tokens with
    # plain values serializable, as SLY tokens already are without an owner.
    if adapter == "ply":
        tok.lexer = None
        tok.lexpos = 17
    else:
        tok.index = 17
        tok.end = 99
    tok.lineno = 7

    result = operation(tok)
    clone = pickle.loads(result) if operation is pickle.dumps else result
    assert type(clone) is type(tok)
    assert clone is not tok
    assert clone.type == "error"
    assert clone.lineno == 7
    assert token_position(adapter, clone) == 17
    assert clone.value == "？？"
    assert tok.value == "？？"
    if adapter == "ply":
        assert clone.lexer is None
    else:
        assert clone.end == 99


@pytest.mark.parametrize("adapter", ["ply", "sly"])
@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_normal_token_copy_protocol_keeps_deleted_value_and_mutable_value_behavior(adapter, operation):
    lexer = make_lexer(adapter, lambda tok, owner: None)
    tok = scan(adapter, lexer, "word")[0]
    if adapter == "ply":
        tok.lexer = None
    value = ["assigned"]
    tok.value = value
    result = operation(tok)
    clone = pickle.loads(result) if operation is pickle.dumps else result
    assert clone.value == value
    assert (clone.value is value) == (operation is copy.copy)

    del tok.value
    result = operation(tok)
    clone = pickle.loads(result) if operation is pickle.dumps else result
    assert type(clone) is type(tok)
    with pytest.raises(AttributeError, match="has no attribute 'value'"):
        _ = clone.value


@pytest.mark.parametrize("adapter", ["ply", "sly"])
@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_copy_protocol_propagates_suffix_attribute_error_instead_of_omitting_value(adapter, operation):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    source = TrackedText("？")
    assert scan(adapter, lexer, source) == []
    tok = saved[0]
    if adapter == "ply":
        tok.lexer = None

    def fail():
        raise AttributeError("suffix access failed")

    source.on_slice = fail
    with pytest.raises(AttributeError, match="suffix access failed"):
        operation(tok)
    source.on_slice = None
    assert tok.value == "？"
    assert source.slices == [slice(0, None), slice(0, None)]


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_pickle_state_keeps_original_public_field_names(adapter):
    lexer = make_lexer(adapter, lambda tok, owner: None)
    tok = scan(adapter, lexer, "word")[0]
    if adapter == "ply":
        tok.lexer = None
        fields = {"type", "value", "lineno", "lexpos", "lexer"}
    else:
        fields = {"type", "value", "lineno", "index", "end"}
    state = tok.__reduce_ex__(4)[2]
    assert state[0] is None
    assert set(state[1]) == fields
    assert state[1]["value"] == "word"
    del tok.value
    assert set(tok.__reduce_ex__(4)[2][1]) == fields - {"value"}


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_pickles_created_with_original_public_slots_still_load(adapter, monkeypatch):
    module = importlib.import_module(f"ply_rs.compat.{adapter}.lex")
    class_name = "LexToken" if adapter == "ply" else "Token"
    token_type = getattr(module, class_name)
    values = {"type": "LEGACY", "value": ["saved"], "lineno": 9}
    if adapter == "ply":
        values.update(lexpos=17, lexer=None)
    else:
        values.update(index=17, end=23)
    legacy_type = type(class_name, (), {
        "__module__": module.__name__, "__slots__": tuple(values),
    })
    legacy_token = legacy_type()
    for name, value in values.items():
        setattr(legacy_token, name, value)
    with monkeypatch.context() as context:
        context.setattr(module, class_name, legacy_type)
        payload = pickle.dumps(legacy_token)

    restored = pickle.loads(payload)
    assert type(restored) is token_type
    for name, value in values.items():
        assert getattr(restored, name) == value

class ExtendedPlyToken(LexToken):
    __slots__ = ("extra", "__private", "__dict__")


class ExtendedSlyToken(Token):
    __slots__ = ("extra", "__private", "__dict__")


@pytest.mark.parametrize("token_class", [ExtendedPlyToken, ExtendedSlyToken])
@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_token_subclass_copy_keeps_dictionary_and_additional_slots(token_class, operation):
    tok = token_class()
    tok.type = "WORD"
    tok.value = ["value"]
    tok.extra = ["slot"]
    tok.details = ["dictionary"]
    private_name = f"_{token_class.__name__}__private"
    setattr(tok, private_name, ["private"])
    result = operation(tok)
    clone = pickle.loads(result) if operation is pickle.dumps else result
    assert type(clone) is token_class
    assert clone.value == ["value"]
    assert clone.extra == ["slot"]
    assert clone.details == ["dictionary"]
    assert getattr(clone, private_name) == ["private"]
    assert (clone.extra is tok.extra) == (operation is copy.copy)
    assert (clone.details is tok.details) == (operation is copy.copy)


@pytest.mark.parametrize("token_class", [LexToken, Token])
def test_value_access_does_not_inspect_the_stored_objects_class(token_class):
    class Value:
        @property
        def __class__(self):
            raise AssertionError("stored value class was inspected")

    value = Value()
    tok = token_class()
    tok.value = value
    assert tok.value is value

