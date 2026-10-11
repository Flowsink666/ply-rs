"""
Unit tests for the Modern class-based Lexer and Parser API.
"""

import pytest
from rply.modern.lexer import Lexer, token
from rply.modern.parser import Parser, rule


class CalcLexer(Lexer):
    ignore = " \t"
    literals = {"(", ")"}

    @token(r"\d+")
    def NUMBER(self, tok):
        tok.value = int(tok.value)
        return tok

    PLUS = r"\+"
    MINUS = r"-"
    TIMES = r"\*"
    DIVIDE = r"/"


class CalcParser(Parser):
    tokens = CalcLexer.tokens
    precedence = [
        ("left", ["PLUS", "MINUS"]),
        ("left", ["TIMES", "DIVIDE"]),
    ]

    @rule("expr : expr PLUS expr")
    def add(self, p):
        return p[1] + p[3]

    @rule("expr : expr MINUS expr")
    def sub(self, p):
        return p[1] - p[3]

    @rule("expr : expr TIMES expr")
    def mul(self, p):
        return p[1] * p[3]

    @rule("expr : expr DIVIDE expr")
    def div(self, p):
        return p[1] // p[3]

    @rule("expr : '(' expr ')'")
    def group(self, p):
        return p[2]

    @rule("expr : NUMBER")
    def num(self, p):
        return p[1]


def test_modern_lexer():
    lexer = CalcLexer()
    tokens = list(lexer.tokenize("42 + (10 - 3) * 5"))
    types = [t.type for t in tokens]
    values = [t.value for t in tokens]

    assert types == ["NUMBER", "PLUS", "(", "NUMBER", "MINUS", "NUMBER", ")", "TIMES", "NUMBER"]
    assert values == [42, "+", "(", 10, "-", 3, ")", "*", 5]


def test_modern_parser():
    lexer = CalcLexer()
    parser = CalcParser()

    res1 = parser.parse(lexer.tokenize("2 + 3 * 4"))
    assert res1 == 14

    res2 = parser.parse(lexer.tokenize("(2 + 3) * 4"))
    assert res2 == 20

    res3 = parser.parse(lexer.tokenize("100 / 5 / 2"))
    assert res3 == 10


def test_bnf_separator_and_explicit_value_action():
    class BnfParser(Parser):
        tokens = {"NUMBER"}

        @rule("expr ::= NUMBER", action_style="values")
        def number(self, value):
            return value + 1

    assert BnfParser().parse(CalcLexer().tokenize("42")) == 43
