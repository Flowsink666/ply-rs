"""Token ingestion, source spans, and observable property access."""

from types import SimpleNamespace

import pytest

from ply_rs import Grammar, LRParser, LRTable, LexerEngine, Token
from ply_rs.backends import native_core

from tests.fixtures.core import parser_for


def test_public_and_native_tokens_compare_in_both_directions():
    from ply_rs._core_rs import Token as NativeToken

    public = Token("N", [1, 2])
    native = NativeToken("N", [1, 2])
    assert public == native and native == public
    assert public != NativeToken("N", [3])
    assert NativeToken("N", [3]) != public
    assert native.__eq__(object()) is NotImplemented
    public.type = 7
    assert not (public == native or native == public)


def test_token_with_positions_does_not_need_string_conversion():
    class Value:
        def __str__(self):
            raise AssertionError("unnecessary value conversion")

    class PositionedToken:
        type = "NUMBER"
        value = Value()
        lineno = 1
        lexpos = 0
        end = 1

        def __str__(self):
            raise AssertionError("unnecessary token conversion")

    result = parser_for(["NUMBER"], lambda p: p[1]).parse([PositionedToken()])
    assert result is PositionedToken.value


def test_native_lexer_token_mutations_reach_parser():
    engine = LexerEngine()

    def number(tok):
        tok.type = "NUMBER"
        tok.value = 42
        tok.lineno = 8
        tok.lexpos = 5
        tok.end = 7
        return tok

    engine.add_rule("INITIAL", "WORD", r"\w+", number)

    def action(p):
        return p[1], p.lineno(1), p.lexspan(1)

    assert parser_for(["NUMBER"], action).parse(engine.tokenize("中文")) == (42, 8, (5, 7))


def test_lhs_position_edits_propagate_to_parent_rule():
    grammar = Grammar({"N"})
    grammar.add_production("s", ["value"], lambda p: (p.linespan(1), p.lexspan(1)))

    def value(p):
        p.set_lineno(0, 99)
        p.set_lexpos(0, 123)
        p.slice[0].endlineno = 100
        p.slice[0].endlexpos = 130
        return p[1]

    grammar.add_production("value", ["N"], value)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    assert parser.parse([Token("N", "abc", lineno=2, lexpos=5)]) == ((99, 100), (123, 130))


@pytest.mark.parametrize("attribute,exception_type", [
    *[(attribute, RuntimeError) for attribute in ("type", "value", "lineno", "lexpos", "index", "end")],
    ("value", StopIteration),
])
def test_token_property_exceptions_propagate_without_running_actions(attribute, exception_type):
    class ExplodingToken:
        type = "N"
        value = 1
        lineno = 1
        lexpos = 0
        end = 1

        def __getattribute__(self, name):
            if attribute == "index" and name == "lexpos":
                raise AttributeError(name)
            if name == attribute:
                raise exception_type(f"cannot read {attribute}")
            return super().__getattribute__(name)

        def __str__(self):
            return "N"

    calls = []
    parser = parser_for(["N"], lambda p: calls.append(p[1]))
    with pytest.raises(exception_type, match=f"cannot read {attribute}"):
        parser.parse([ExplodingToken()])
    assert calls == []


@pytest.mark.parametrize("value,positions,expected", [
    pytest.param("abc", {"lineno": 2, "lexpos": 5}, ((2, 2), (5, 8)), id="ascii-inferred-end"),
    pytest.param("中文🙂", {"lineno": 2, "lexpos": 5}, ((2, 2), (5, 8)), id="unicode-inferred-end"),
    pytest.param(123, {"lineno": 2, "lexpos": 5}, ((2, 2), (5, 8)), id="numeric-inferred-end"),
    pytest.param(123, {}, ((1, 1), (0, 3)), id="default-positions"),
    pytest.param(123, {"index": 9}, ((1, 1), (9, 12)), id="index-fallback"),
    pytest.param(123, {"lineno": 3, "lexpos": 2, "index": 999}, ((3, 3), (2, 5)), id="lexpos-precedes-index"),
    pytest.param(123, {"lineno": 4, "lexpos": 2, "end": 17}, ((4, 4), (2, 17)), id="explicit-end"),
])
def test_generic_token_spans_use_public_positions_and_value(value, positions, expected):
    parser = parser_for(["N"], lambda p: (p.linespan(1), p.lexspan(1)))
    assert parser.parse([SimpleNamespace(type="N", value=value, **positions)]) == expected


@pytest.mark.parametrize("attribute,exception_type", [
    *[(attribute, RuntimeError) for attribute in ("lineno", "lexpos", "index", "end")],
    ("lexpos", StopIteration),
])
def test_native_position_conversion_exceptions_propagate(attribute, exception_type):
    class ExplodingInteger:
        def __index__(self):
            raise exception_type(f"cannot convert {attribute}")

    positions = {"lineno": 1, "lexpos": 0, "end": 1}
    if attribute == "index":
        del positions["lexpos"]
    positions[attribute] = ExplodingInteger()
    calls = []
    parser = parser_for(["N"], lambda p: calls.append(p[1]))
    with pytest.raises(exception_type, match=f"cannot convert {attribute}"):
        parser.parse([SimpleNamespace(type="N", value=1, **positions)])
    assert calls == []


@pytest.mark.parametrize("frozen,exception_type", [(False, RuntimeError), (True, StopIteration)])
def test_lazy_error_value_exceptions_propagate_before_reduction(frozen, exception_type):
    class ExplodingText(str):
        def __getitem__(self, key):
            if isinstance(key, slice):
                raise exception_type("cannot materialize error text")
            return super().__getitem__(key)

    error_calls, action_calls = [], []

    def recover(tok):
        error_calls.append(tok.lexpos)
        tok.type = "N"
        tok.lexer.skip()
        return tok

    lexer = LexerEngine()
    lexer.set_error_func("INITIAL", recover)
    parser = parser_for(["N"], lambda p: action_calls.append(p[1]), frozen=frozen)
    with pytest.raises(exception_type, match="cannot materialize error text"):
        parser.parse(lexer.tokenize(ExplodingText("?")))
    assert error_calls == [0]
    assert action_calls == []
    assert (lexer.data, lexer.pos, lexer.length) == ("?", 1, 1)


def test_shifted_tokens_remain_snapshots_of_the_consumed_value():
    grammar = Grammar({"N"})
    grammar.add_production("pair", ["N", "N"], lambda p: (p[1], p[2]))
    grammar.set_start("pair")
    parser = LRParser(LRTable(grammar))
    first = Token("N", 1)

    def tokens():
        yield first
        first.value = 99
        yield Token("N", 2)

    assert parser.parse(tokens()) == (1, 2)


def test_direct_native_token_counts_surrogate_values():
    assert native_core.Token("CHAR", "\ud800a").end == 2


def test_native_token_length_uses_string_content_without_subclass_hooks():
    class Text(str):
        def __len__(self):
            raise AssertionError("token construction called subclass __len__")

    assert native_core.Token("CHAR", Text("\ud800a")).end == 2


def test_token_comparison_defers_unknown_objects():
    class Other:
        type = "N"
        value = 1

        def __eq__(self, other):
            return "reflected"

    tok = Token("N", 1)
    assert tok.__eq__(Other()) is NotImplemented
    assert (tok == Other()) == "reflected"


def test_generic_token_end_overflow_raises_python_exception():
    from types import SimpleNamespace
    from ply_rs.core.lexer_config import MAX_LEXER_COUNTER

    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    with pytest.raises(OverflowError):
        parser.parse([SimpleNamespace(type="N", value="a", lexpos=MAX_LEXER_COUNTER)])
    assert parser._sessions == []
