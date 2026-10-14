"""Public token descriptor overrides keep their copy and pickle behavior."""

import copy
import pickle

import pytest

from rply.compat.ply.lex import LexToken
from rply.compat.sly.lex import Token


class ValueSlotPlyToken(LexToken):
    __slots__ = ("value",)


class ValueSlotSlyToken(Token):
    __slots__ = ("value",)


class MetadataValueSlotPlyToken(LexToken):
    __slots__ = ("_value",)


class MetadataValueSlotSlyToken(Token):
    __slots__ = ("_value",)


class PublicValueState:
    @property
    def value(self):
        return self._cached

    @value.setter
    def value(self, value):
        self._cached = value

    def __getstate__(self):
        # Export the inherited public token fields, keeping the implementation
        # cache out of the serialized dictionary. Restore uses the setter.
        dictionary, slots = super().__getstate__()
        return None, slots


class PropertyPlyToken(PublicValueState, LexToken):
    pass


class PropertySlyToken(PublicValueState, Token):
    pass


class SingleReadMetadata:
    @property
    def lineno(self):
        self._metadata_reads += 1
        if self._metadata_reads > 1:
            raise RuntimeError("metadata read twice")
        return self._line

    @lineno.setter
    def lineno(self, value):
        self._line = value


class MetadataPlyToken(SingleReadMetadata, LexToken):
    pass


class MetadataSlyToken(SingleReadMetadata, Token):
    pass


def pickle_roundtrip(token):
    return pickle.loads(pickle.dumps(token))


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle_roundtrip])
@pytest.mark.parametrize("token_class", [
    ValueSlotPlyToken, ValueSlotSlyToken, PropertyPlyToken, PropertySlyToken,
])
def test_overridden_public_value_survives_copy_and_pickle(token_class, operation):
    original = token_class()
    original.type = "WORD"
    original.value = ["saved"]
    clone = operation(original)
    assert type(clone) is token_class
    assert clone.value == ["saved"]
    assert (clone.value is original.value) == (operation is copy.copy)


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle_roundtrip])
@pytest.mark.parametrize("token_class", [
    MetadataValueSlotPlyToken, MetadataValueSlotSlyToken,
])
def test_subclass_value_metadata_slot_is_independent(token_class, operation):
    original = token_class()
    original.value = ["payload"]
    original._value = ["metadata"]
    assert original.value == ["payload"]
    clone = operation(original)
    assert clone.value == ["payload"]
    assert clone._value == ["metadata"]
    assert (clone.value is original.value) == (operation is copy.copy)
    assert (clone._value is original._value) == (operation is copy.copy)


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle_roundtrip])
@pytest.mark.parametrize("token_class", [MetadataPlyToken, MetadataSlyToken])
def test_public_metadata_getter_runs_once_per_serialization(token_class, operation):
    original = token_class()
    original._metadata_reads = 0
    original.lineno = 7
    clone = operation(original)
    assert original._metadata_reads == 1
    assert clone._line == 7


@pytest.mark.parametrize("token_class", [MetadataPlyToken, MetadataSlyToken])
def test_public_metadata_attribute_error_is_omitted_like_a_missing_slot(token_class):
    class MissingMetadata(token_class):
        @property
        def lineno(self):
            raise AttributeError("metadata missing")

        @lineno.setter
        def lineno(self, value):
            pass

    original = MissingMetadata()
    dictionary, slots = original.__getstate__()
    assert "lineno" not in slots
