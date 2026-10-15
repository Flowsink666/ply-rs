"""Action dispatch, defaults, exceptions, and reduction ordering."""

import pytest

from ply_rs import Grammar, LRParser, LRTable, Token
from ply_rs.core.grammar import GrammarError

from tests.fixtures.core import parser_for


@pytest.mark.parametrize("rhs,action,style,values,expected", [
    pytest.param(["LEFT", "RIGHT"], lambda left, right: left + right,
                 "auto", [3, 4], 7, id="inferred-multiple-values"),
    pytest.param(["NUMBER"], lambda value: value + 1,
                 "values", [42], 43, id="explicit-single-value"),
    pytest.param([], lambda: 42, "auto", [], 42, id="inferred-empty-production"),
])
def test_value_action_dispatch(rhs, action, style, values, expected):
    parser = parser_for(rhs, action, style)
    assert parser.parse([Token(kind, value) for kind, value in zip(rhs, values)]) == expected


def test_invalid_action_style_is_rejected():
    with pytest.raises(GrammarError, match="action_style"):
        parser_for(["NUMBER"], lambda p: p[1], "unknown")


@pytest.mark.parametrize("rhs,tokens,expected", [
    ([], [], None),
    (["NUMBER"], [Token("NUMBER", 42)], 42),
])
@pytest.mark.parametrize("style", ["default", "values", "proxy"])
def test_default_and_none_value_actions(rhs, tokens, expected, style):
    action = (lambda *args: None) if style != "default" else None
    parser = parser_for(rhs, action, style if style != "default" else "auto")
    assert parser.parse(tokens) == expected


class FalseyCallback:
    def __init__(self, callback):
        self.callback = callback

    def __bool__(self):
        return False

    def __call__(self, *args):
        return self.callback(*args)


@pytest.mark.parametrize("action_style", ["proxy", "values"])
def test_falsey_production_and_error_callbacks_are_called(action_style):
    grammar = Grammar({"N", "BAD"})
    action = (lambda p: p[1] + 1) if action_style == "proxy" else (lambda value: value + 1)
    grammar.add_production("s", ["N"], FalseyCallback(action), action_style=action_style)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar), error_func=FalseyCallback(lambda token: ("error", token.value)))
    assert parser.parse([Token("N", 1)]) == 2
    assert parser.parse([Token("BAD", "?")]) == ("error", "?")


def test_invalid_lookahead_is_checked_before_a_single_possible_reduction():
    calls = []
    grammar = Grammar({"N", "SEMI", "BAD"})
    grammar.add_production("s", ["item", "SEMI"], lambda p: p[1])
    grammar.add_production("item", ["N"], lambda p: calls.append(p[1]))
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    with pytest.raises(SyntaxError):
        parser.parse([Token("N", 7), Token("BAD", "?")])
    assert calls == []
