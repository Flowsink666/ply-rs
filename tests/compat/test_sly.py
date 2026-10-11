"""SLY compatibility tests."""

from rply.compat import sly
from tests.fixtures.sly_calculator import build_calculator

SlyCalcLexer, SlyCalcParser = build_calculator(sly)


def test_sly_compat_calculator():
    lexer = SlyCalcLexer()
    parser = SlyCalcParser()

    assert parser.parse(lexer.tokenize("10 + 20 * 3")) == 70
    assert parser.parse(lexer.tokenize("(10 + 20) * 3")) == 90
    assert parser.parse(lexer.tokenize("50 - 20 - 5")) == 25
    assert parser.parse(lexer.tokenize("100 / 2 / 5")) == 10


def test_sly_action_view_uses_rhs_positions():
    class PositionLexer(sly.Lexer):
        tokens = {NUMBER}
        ignore = " \n"

        @_(r"\d+")
        def NUMBER(self, tok):
            tok.value = int(tok.value)
            return tok

    class PositionParser(sly.Parser):
        tokens = PositionLexer.tokens

        @_("NUMBER")
        def expr(self, p):
            return p[0], len(p), p.NUMBER, p.lineno, p.index

    assert PositionParser().parse(PositionLexer().tokenize("\n  42")) == (42, 1, 42, 2, 3)
