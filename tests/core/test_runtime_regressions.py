"""Behavioral regressions shared by the Python and native runtimes."""

import re
from typing import Union, get_type_hints

import pytest

from rply import Grammar, LexerEngine, LRParser, LRTable, Token
from rply.core.grammar import GrammarError
from rply.core.production import ParsedSymbol, ProductionProxy
from rply.core.tokens import LexError
from rply.compat.ply.yacc import YaccProduction


def parser_for(rhs, action, action_style="auto"):
    grammar = Grammar(set(rhs))
    grammar.add_production("result", rhs, func=action, action_style=action_style)
    grammar.set_start("result")
    return LRParser(LRTable(grammar))


def test_action_type_error_preserves_exception_and_runs_once():
    calls = []
    failure = TypeError("failure inside action")

    def action(proxy):
        calls.append(proxy[1])
        raise failure

    parser = parser_for(["NUMBER"], action)
    with pytest.raises(TypeError) as caught:
        parser.parse([Token("NUMBER", 42)])
    assert caught.value is failure
    assert calls == [42]


def test_multiple_values_action_is_selected_from_signature():
    parser = parser_for(["LEFT", "RIGHT"], lambda left, right: left + right)
    assert parser.parse([Token("LEFT", 3), Token("RIGHT", 4)]) == 7


def test_single_value_action_can_be_explicit():
    parser = parser_for(["NUMBER"], lambda value: value + 1, "values")
    assert parser.parse([Token("NUMBER", 42)]) == 43


def test_empty_production_accepts_zero_argument_action():
    assert parser_for([], lambda: 42).parse([]) == 42


def test_invalid_action_style_is_rejected():
    with pytest.raises(GrammarError, match="action_style"):
        parser_for(["NUMBER"], lambda p: p[1], "unknown")


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


def test_named_aliases_keep_distinct_values_and_follow_assignments():
    proxy = ProductionProxy([
        ParsedSymbol("result"), ParsedSymbol("expr", 3),
        ParsedSymbol("PLUS", "+"), ParsedSymbol("expr", 4),
    ])
    assert (proxy.expr, proxy.expr0, proxy.expr1, proxy.PLUS) == (3, 3, 4, "+")
    proxy[1] = 9
    assert proxy.expr0 == 9
    with pytest.raises(AttributeError, match="missing"):
        proxy.missing


def test_production_proxy_index_annotation_and_slicing():
    assert get_type_hints(ProductionProxy.__getitem__)["n"] == Union[int, slice]
    assert get_type_hints(YaccProduction.__getitem__)["n"] == Union[int, slice]
    proxy = ProductionProxy([
        ParsedSymbol("result"), ParsedSymbol("NUMBER", 3), ParsedSymbol("NUMBER", 4),
    ])
    assert proxy[1] == 3
    assert proxy[1:] == [3, 4]
    assert proxy[::-1] == [4, 3, None]
    assert [symbol.value for symbol in proxy.slice] == [None, 3, 4]
    production = YaccProduction(proxy)
    assert production[1] == 3
    assert production[1:] == [3, 4]
    assert production[::-1] == [4, 3, None]
    assert production.slice is proxy.slice


@pytest.mark.parametrize("pattern,flags,text", [
    ("abc", re.IGNORECASE, "ABC"),
    ("abc$", re.MULTILINE, "abc\n"),
    ("a.b", re.DOTALL, "a\nb"),
    ("a b c # comment", re.VERBOSE, "abc"),
    (r"\w+", re.UNICODE, "中文"),
    ("a . b # comment", re.IGNORECASE | re.DOTALL | re.VERBOSE, "A\nB"),
])
def test_regex_flags(pattern, flags, text):
    engine = LexerEngine()
    engine.add_rule("INITIAL", "MATCH", pattern, flags=flags)
    engine.input(text)
    expected = text.rstrip("\n") if flags == re.MULTILINE else text
    assert engine.token().value == expected


def test_multiline_flag_does_not_skip_unmatched_prefix():
    engine = LexerEngine()
    engine.add_rule("INITIAL", "WORD", "abc", flags=re.MULTILINE)
    engine.input("?\nabc")
    with pytest.raises(LexError):
        engine.token()
    assert engine.pos == 0


def test_registering_initial_rule_updates_inclusive_state():
    engine = LexerEngine()
    engine.add_state("OTHER", "inclusive")
    engine.add_rule("INITIAL", "A", "a")
    engine.input("ab")
    engine.begin("OTHER")
    assert engine.token().type == "A"
    engine.add_rule("INITIAL", "B", "b")
    assert engine.token().type == "B"


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


@pytest.mark.parametrize("rhs,tokens,expected", [
    ([], [], None),
    (["NUMBER"], [Token("NUMBER", 42)], 42),
])
@pytest.mark.parametrize("style", ["default", "values", "proxy"])
def test_default_and_none_value_actions(rhs, tokens, expected, style):
    action = (lambda *args: None) if style != "default" else None
    parser = parser_for(rhs, action, style if style != "default" else "auto")
    assert parser.parse(tokens) == expected


def test_value_action_exception_preserves_identity_and_runs_once():
    calls = []
    failure = TypeError("value action failure")

    def action(value):
        calls.append(value)
        raise failure

    with pytest.raises(TypeError) as caught:
        parser_for(["NUMBER"], action, "values").parse([Token("NUMBER", 42)])
    assert caught.value is failure
    assert calls == [42]


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


def test_saved_proxies_and_none_return_assignment_remain_valid():
    saved = []

    def action(p):
        saved.append(p)
        p[0] = p[1] + 1

    parser = parser_for(["NUMBER"], action)
    assert parser.parse([Token("NUMBER", 3)]) == 4
    assert parser.parse([Token("NUMBER", 8)]) == 9
    assert [(p[0], p[1]) for p in saved] == [(4, 3), (9, 8)]


@pytest.mark.parametrize("change", ["action", "goto"])
def test_modified_public_table_is_used_by_new_parser(change):
    grammar = Grammar({"NUMBER"})
    grammar.add_production("result", ["NUMBER"])
    grammar.set_start("result")
    table = LRTable(grammar)
    # Creating multiple parsers must leave the shared table unchanged.
    for _ in range(2):
        assert LRParser(table).parse([Token("NUMBER", 42)]) == 42
    if change == "action":
        table.action[0]["NUMBER"] = None
        with pytest.raises(SyntaxError):
            LRParser(table).parse([Token("NUMBER", 42)])
    else:
        table.goto[0]["result"] = 0
        with pytest.raises(SyntaxError):
            LRParser(table).parse([Token("NUMBER", 42)])


def test_values_action_can_be_rebound_between_parses():
    grammar = Grammar({"NUMBER"})
    grammar.add_production("result", ["NUMBER"], func="number", action_style="values")
    grammar.set_start("result")
    production = grammar.Productions[1]
    production.bind({"number": lambda value: value + 1})
    parser = LRParser(LRTable(grammar))
    assert parser.parse([Token("NUMBER", 42)]) == 43
    production.func = lambda value: value + 2
    assert parser.parse([Token("NUMBER", 42)]) == 44


@pytest.mark.parametrize("rhs", [[], ["C"]])
def test_action_negative_indices_read_remaining_stack(rhs):
    grammar = Grammar({"A", "B", "C"})
    grammar.add_production("result", ["A", "B", "seen"], lambda p: p[3])

    def seen(p):
        with pytest.raises(IndexError):
            p[-4]
        # Values to the left of the reduced RHS are on the remaining stack.
        return p[-2], p[-1], list(p[1:])

    grammar.add_production("seen", rhs, seen)
    grammar.set_start("result")
    tokens = [Token("A", 10), Token("B", 20)]
    if rhs:
        tokens.append(Token("C", 30))
    parser = LRParser(LRTable(grammar))
    assert parser.parse(tokens) == (10, 20, [30] if rhs else [])
    assert parser.parse(tokens) == (10, 20, [30] if rhs else [])
