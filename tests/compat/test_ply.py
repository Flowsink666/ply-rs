"""PLY compatibility tests."""

from rply.compat.ply import lex, yacc
from tests.fixtures import ply_calculator as spec


def test_ply_compat_calculator():
    lexer = lex.lex(module=spec)
    parser = yacc.yacc(module=spec)

    assert parser.parse("3 + 4 * 10", lexer=lexer) == 43
    assert parser.parse("(3 + 4) * 10", lexer=lexer) == 70
    assert parser.parse("-5 + 10", lexer=lexer) == 5
    assert parser.parse("100 / 10 / 2", lexer=lexer) == 5


def test_ply_lexer_iteration():
    lexer = lex.lex(module=spec)
    lexer.input("123 + 456")
    toks = list(lexer)
    assert len(toks) == 3
    assert toks[0].type == "NUMBER" and toks[0].value == 123
    assert toks[1].type == "PLUS"
    assert toks[2].type == "NUMBER" and toks[2].value == 456


def test_ply_callback_owns_public_lexer():
    owners = []

    def t_NUMBER(tok):
        r"\d+"
        owners.append(tok.lexer)
        if tok.lexpos == 0:
            tok.lexer.skip(1)
        return tok

    lexer = lex.lex(module={"tokens": ("NUMBER",), "t_NUMBER": t_NUMBER})
    lexer.input("12?34")
    assert [t.value for t in lexer] == ["12", "34"]
    assert owners == [lexer, lexer]
