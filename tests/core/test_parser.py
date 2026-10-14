"""
Unit tests for core LR runtime parser execution.
"""

import pytest
from ply_rs.core.grammar import Grammar
from ply_rs.core.lr_table import LRTable
from ply_rs.core.lr_parser import LRParser
from ply_rs.core.lexer_engine import Token


def test_core_parser_arithmetic():
    g = Grammar(terminals={"PLUS", "MINUS", "TIMES", "DIVIDE", "NUMBER"})
    g.set_precedence([
        ("left", ["PLUS", "MINUS"]),
        ("left", ["TIMES", "DIVIDE"]),
    ])

    def p_add(p):
        return p[1] + p[3]

    def p_sub(p):
        return p[1] - p[3]

    def p_mul(p):
        return p[1] * p[3]

    def p_div(p):
        return p[1] // p[3]

    def p_num(p):
        return p[1]

    g.add_production("expr", ["expr", "PLUS", "expr"], func=p_add)
    g.add_production("expr", ["expr", "MINUS", "expr"], func=p_sub)
    g.add_production("expr", ["expr", "TIMES", "expr"], func=p_mul)
    g.add_production("expr", ["expr", "DIVIDE", "expr"], func=p_div)
    g.add_production("expr", ["NUMBER"], func=p_num)
    g.set_start("expr")

    table = LRTable(g)
    parser = LRParser(table)

    # 10 - 5 - 2 should be (10 - 5) - 2 = 3 (left associative)
    tokens = [
        Token("NUMBER", 10),
        Token("MINUS", "-"),
        Token("NUMBER", 5),
        Token("MINUS", "-"),
        Token("NUMBER", 2),
    ]
    assert parser.parse(tokens) == 3

    # 2 + 3 * 4 should be 2 + (3 * 4) = 14 (* higher precedence than +)
    tokens = [
        Token("NUMBER", 2),
        Token("PLUS", "+"),
        Token("NUMBER", 3),
        Token("TIMES", "*"),
        Token("NUMBER", 4),
    ]
    assert parser.parse(tokens) == 14


def test_core_parser_syntax_error():
    g = Grammar(terminals={"NUMBER", "PLUS"})
    g.add_production("expr", ["expr", "PLUS", "expr"], func=lambda p: p[1] + p[3])
    g.add_production("expr", ["NUMBER"], func=lambda p: p[1])
    g.set_start("expr")

    table = LRTable(g)
    parser = LRParser(table)

    # Invalid syntax: 2 + + 3
    bad_tokens = [
        Token("NUMBER", 2),
        Token("PLUS", "+"),
        Token("PLUS", "+"),
        Token("NUMBER", 3),
    ]
    with pytest.raises(SyntaxError):
        parser.parse(bad_tokens)


def test_parser_error_recovery_unrecoverable_eof():
    """Verify that error recovery on unexpected EOF does not enter an infinite loop."""
    g = Grammar(terminals={"ID", "PLUS", "SEMI", "error"})
    g.add_production("stmt", ["ID", "SEMI"])
    g.add_production("stmt", ["error", "SEMI"])
    g.set_start("stmt")

    table = LRTable(g)
    parser = LRParser(table)

    bad_tokens = [Token("PLUS", "+"), Token("PLUS", "+")]
    with pytest.raises(SyntaxError):
        parser.parse(bad_tokens)
