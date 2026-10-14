"""
Unit tests for the Modern class-based Lexer and Parser API.
"""

import pytest
from ply_rs.modern.lexer import Lexer, token
from ply_rs.modern.parser import Parser, rule
from ply_rs import Token


def test_decorated_token_priority_precedes_string_rules():
    class PriorityLexer(Lexer):
        A = "a"
        B = "b"

        @token("ab", priority=100)
        def AB(self, tok):
            return tok

    assert [(t.type, t.value) for t in PriorityLexer().tokenize("ab")] == [("AB", "ab")]


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


@pytest.mark.parametrize("string_rule", [False, True])
def test_lexer_subclass_replaces_inherited_rule(string_rule):
    class Base(Lexer):
        PLUS = r"\+"

        @token(r"\d+")
        def NUMBER(self, tok):
            tok.value = "base"
            return tok

    class Child(Base):
        if string_rule:
            NUMBER = r"\d+"
        else:
            @token(r"\d+")
            def NUMBER(self, tok):
                tok.value = "child"
                return tok

    assert [(t.type, t.value) for t in Child().tokenize("42+")] == [
        ("NUMBER", "42" if string_rule else "child"), ("PLUS", "+"),
    ]
    assert next(Base().tokenize("42")).value == "base"


def test_parser_subclass_replaces_all_rules_on_inherited_action():
    class Base(Parser):
        tokens = {"NUMBER", "WORD"}

        @rule("expr : NUMBER", "expr : WORD")
        def value(self, p):
            return "base"

    class Child(Base):
        @rule("expr : NUMBER", "expr : WORD")
        def value(self, p):
            return "child", p[1]

    parser = Child()
    assert parser.table.rr_conflicts == []
    assert parser.parse([Token("NUMBER", 42)]) == ("child", 42)
    assert parser.parse([Token("WORD", "hello")]) == ("child", "hello")
    assert Base().parse([Token("NUMBER", 42)]) == "base"


def test_rule_overrides_preserve_lexer_priority_and_implicit_start():
    class BaseLexer(Lexer):
        KEYWORD = "if"
        ID = "[a-z]+"

    class ChildLexer(BaseLexer):
        KEYWORD = "else"

    assert [(t.type, t.value) for t in ChildLexer().tokenize("else")] == [("KEYWORD", "else")]

    class BaseParser(Parser):
        tokens = {"NUMBER"}

        @rule("expr : number")
        def expr(self, p):
            return "base", p[1]

        @rule("number : NUMBER")
        def number(self, p):
            return p[1]

    class ChildParser(BaseParser):
        @rule("expr : number")
        def expr(self, p):
            return "child", p[1]

    assert ChildParser().parse([Token("NUMBER", 42)]) == ("child", 42)


def test_diamond_inheritance_uses_mro_without_duplicate_rules():
    class BaseLexer(Lexer):
        NUMBER = r"\d+"

    class LeftLexer(BaseLexer):
        pass

    class RightLexer(BaseLexer):
        @token(r"\d+")
        def NUMBER(self, tok):
            tok.value = int(tok.value)
            return tok

    class CombinedLexer(LeftLexer, RightLexer):
        pass

    assert next(CombinedLexer().tokenize("42")).value == 42

    class BaseParser(Parser):
        tokens = {"NUMBER"}

        @rule("expr : NUMBER")
        def value(self, p):
            return "base"

    class LeftParser(BaseParser):
        pass

    class RightParser(BaseParser):
        @rule("expr : NUMBER")
        def value(self, p):
            return p[1]

    class CombinedParser(LeftParser, RightParser):
        pass

    parser = CombinedParser()
    assert parser.table.rr_conflicts == []
    assert parser.parse(CombinedLexer().tokenize("42")) == 42
