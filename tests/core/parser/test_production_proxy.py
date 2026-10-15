"""Production proxy indexing, aliases, assignment, and saved views."""

from typing import Union, get_type_hints

import pytest

from ply_rs import Grammar, LRParser, LRTable, Token
from ply_rs.compat.ply.yacc import YaccProduction
from ply_rs.core.production import ParsedSymbol, ProductionProxy

from tests.fixtures.core import parser_for


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


def test_saved_proxies_and_none_return_assignment_remain_valid():
    saved = []

    def action(p):
        saved.append(p)
        p[0] = p[1] + 1

    parser = parser_for(["NUMBER"], action)
    assert parser.parse([Token("NUMBER", 3)]) == 4
    assert parser.parse([Token("NUMBER", 8)]) == 9
    assert [(p[0], p[1]) for p in saved] == [(4, 3), (9, 8)]


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


@pytest.mark.parametrize("frozen", [False, True])
def test_saved_proxies_are_distinct_within_one_parse(frozen):
    saved = []
    grammar = Grammar({"N"})

    def single(p):
        saved.append(p)
        p[0] = p[1]

    def append(p):
        saved.append(p)
        p[0] = p[1] + p[2]

    grammar.add_production("items", ["items", "N"], append)
    grammar.add_production("items", ["N"], single)
    grammar.set_start("items")
    parser = LRParser(LRTable(grammar), frozen=frozen)
    assert parser.parse([Token("N", value) for value in (1, 2, 3)]) == 6
    assert len({id(proxy) for proxy in saved}) == 3
    assert [list(proxy[:]) for proxy in saved] == [[1, 1], [3, 1, 2], [6, 3, 3]]
