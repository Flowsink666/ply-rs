"""Compatibility recovery keeps the original suffix until value is requested."""

import gc
import weakref

import pytest

from rply.compat.ply import lex as ply_lex
from rply.compat.sly import Lexer as SlyLexer
from rply.compat.sly.lex import Token as SlyToken


class TrackedText(str):
    def __new__(cls, value):
        result = super().__new__(cls, value)
        result.slices = []
        result.on_slice = None
        return result

    def __getitem__(self, key):
        if isinstance(key, slice):
            self.slices.append(key)
            if self.on_slice is not None:
                self.on_slice()
        return super().__getitem__(key)


def make_lexer(adapter, recover):
    if adapter == "ply":
        return ply_lex.lex(module={
            "tokens": ("WORD",), "t_WORD": r"[a-z]+", "t_ignore": " ",
            "t_error": lambda tok: recover(tok, tok.lexer),
        })

    class Words(SlyLexer):
        tokens = {"WORD"}
        WORD = r"[a-z]+"
        ignore = " "

        def error(self, tok):
            return recover(tok, self)

    return Words()


def scan(adapter, lexer, source):
    if adapter == "ply":
        lexer.input(source)
        return list(lexer)
    return list(lexer.tokenize(source))


def token_position(adapter, tok):
    return tok.lexpos if adapter == "ply" else tok.index


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_error_recovery_defers_suffix_copies_and_keeps_public_token_type(adapter):
    saved = []

    def recover(tok, lexer):
        saved.append(tok)
        if adapter == "ply":
            lexer.skip()
        else:
            lexer.index += 1

    lexer = make_lexer(adapter, recover)
    source = TrackedText("？" * 4096)
    assert scan(adapter, lexer, source) == []
    expected_type = ply_lex.LexToken if adapter == "ply" else SlyToken
    assert all(type(tok) is expected_type for tok in saved)
    assert [token_position(adapter, tok) for tok in saved] == list(range(4096))
    assert source.slices == []
    assert saved[17].value == "？" * (4096 - 17)
    assert isinstance(saved[17].value, str)
    assert source.slices == [slice(17, None)]


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_saved_unread_suffix_survives_input_and_position_changes(adapter):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    source = TrackedText("ab？旧")
    assert [tok.value for tok in scan(adapter, lexer, source)] == ["ab"]
    tok = saved[0]
    assert source.slices == []
    assert token_position(adapter, tok) == 2
    tok.lineno = 7
    if adapter == "ply":
        tok.lexpos = 99
        assert tok.lexer is lexer
    else:
        assert tok.end == 4
        tok.index = 99
        tok.end = 123
    assert [tok.value for tok in scan(adapter, lexer, "replacement")] == ["replacement"]
    assert tok.value == "？旧"
    assert tok.value == "？旧"
    assert source.slices == [slice(2, None)]
    if adapter == "ply":
        assert repr(tok) == "LexToken(error,'？旧',7,99)"
        assert str(tok) == repr(tok)
    else:
        assert repr(tok) == "Token(type='error', value='？旧', lineno=7, index=99, end=123)"
        assert tok == SlyToken("error", "？旧")
        assert SlyToken("error", "？旧") == tok


@pytest.mark.parametrize("adapter", ["ply", "sly"])
@pytest.mark.parametrize("operation", ["assign", "delete"])
def test_overwriting_or_deleting_unread_value_does_not_copy(adapter, operation):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    source = TrackedText("？")
    assert scan(adapter, lexer, source) == []
    tok = saved[0]
    if operation == "assign":
        tok.value = None
        assert tok.value is None
    else:
        del tok.value
        with pytest.raises(AttributeError):
            _ = tok.value
        with pytest.raises(AttributeError):
            del tok.value
    replacement = []
    tok.value = replacement
    assert tok.value is replacement
    assert token_position(adapter, tok) == 0
    assert source.slices == []


@pytest.mark.parametrize("adapter", ["ply", "sly"])
@pytest.mark.parametrize("materialized", [False, True])
def test_saved_error_value_and_source_cycles_are_collectible(adapter, materialized):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    lexer.saved = saved
    source = TrackedText("？")
    source.owner = lexer
    assert scan(adapter, lexer, source) == []
    if materialized:
        value = TrackedText("handled")
        value.owner = lexer
        saved[0].value = value
        del value
    reference = weakref.ref(lexer)
    source_reference = weakref.ref(source)
    del lexer, source, saved
    gc.collect()
    assert reference() is None
    assert source_reference() is None


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_error_handler_reentry_preserves_saved_suffix_and_committed_input(adapter):
    saved = []
    nested = []

    def recover(tok, lexer):
        saved.append(tok)
        if adapter == "ply":
            lexer.input("new remaining")
            nested.append(lexer.token().value)
        else:
            nested.append(next(lexer.tokenize("new remaining")).value)

    lexer = make_lexer(adapter, recover)
    source = TrackedText("？old")
    assert [tok.value for tok in scan(adapter, lexer, source)] == ["remaining"]
    assert nested == ["new"]
    assert len(saved) == 1
    assert source.slices == []
    assert saved[0].value == "？old"
    assert source.slices == [slice(0, None)]


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_slice_callback_can_replace_compatibility_value(adapter):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    source = TrackedText("？")
    assert scan(adapter, lexer, source) == []
    assert source.slices == []
    tok = saved[0]
    source.on_slice = lambda: setattr(tok, "value", "from callback")
    assert tok.value == "from callback"
    assert tok.value == "from callback"
    assert source.slices == [slice(0, None)]


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_slice_failures_propagate_only_on_value_read_and_can_retry(adapter):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    source = TrackedText("？")

    def fail():
        raise RuntimeError("slice failed")

    source.on_slice = fail
    assert scan(adapter, lexer, source) == []
    with pytest.raises(RuntimeError, match="slice failed"):
        _ = saved[0].value
    source.on_slice = None
    assert saved[0].value == "？"
    assert source.slices == [slice(0, None), slice(0, None)]


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_returned_error_token_keeps_value_lazy_until_the_consumer_reads_it(adapter):
    saved = []

    def recover(tok, lexer):
        saved.append(tok)
        if adapter == "ply":
            lexer.skip()
        else:
            lexer.index += 1
        return tok

    lexer = make_lexer(adapter, recover)
    source = TrackedText("？")
    emitted = scan(adapter, lexer, source)
    assert len(emitted) == len(saved) == 1
    assert source.slices == []
    expected_type = ply_lex.LexToken if adapter == "ply" else SlyToken
    assert type(emitted[0]) is expected_type
    assert emitted[0].value == "？"
    assert saved[0].value == "？"
    assert source.slices == [slice(0, None)]


@pytest.mark.parametrize("adapter", ["ply", "sly"])
def test_slice_callback_can_delete_value_without_restoring_the_old_suffix(adapter):
    saved = []
    lexer = make_lexer(adapter, lambda tok, owner: saved.append(tok))
    source = TrackedText("？")
    assert scan(adapter, lexer, source) == []
    tok = saved[0]
    source.on_slice = lambda: delattr(tok, "value")
    with pytest.raises(AttributeError, match="has no attribute 'value'"):
        _ = tok.value
    tok.value = "new"
    assert tok.value == "new"
    assert source.slices == [slice(0, None)]


def test_ply_position_and_line_properties_show_callback_updates_immediately():
    trace = []

    def t_WORD(tok):
        r"[a-z]+"
        trace.append((tok.lexer.lexpos, tok.lexer.lineno))
        tok.lexer.skip()
        tok.lexer.lineno = 7
        trace.append((tok.lexer.lexpos, tok.lexer.lineno))
        return tok

    lexer = ply_lex.lex(module={"tokens": ("WORD",), "t_WORD": t_WORD})
    lexer.input("ab?cd?")
    assert (lexer.lexpos, lexer.lineno) == (0, 1)
    assert lexer.token().value == "ab"
    assert (lexer.lexpos, lexer.lineno) == (3, 7)
    assert lexer.token().value == "cd"
    assert (lexer.lexpos, lexer.lineno) == (6, 7)
    assert lexer.token() is None
    assert trace == [(2, 1), (3, 7), (5, 7), (6, 7)]
    lexer.input("x")
    assert (lexer.lexpos, lexer.lineno) == (0, 1)
