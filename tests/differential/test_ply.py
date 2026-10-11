"""Compare token streams and parser results against reference PLY."""

import pytest

from rply.compat.ply import lex, yacc
from tests.fixtures import ply_calculator as spec

pytestmark = pytest.mark.differential

EXPRESSIONS = [
    "1 + 2 * 3", "(1 + 2) * 3", "100 - 50 - 20 - 5",
    "10 * 20 + 30 * 40", "((2 + 3) * (4 + 5)) / 9", "-5 + 10",
]


def tokens(lexer, text):
    lexer.input(text)
    return [(t.type, t.value, t.lineno, t.lexpos) for t in lexer]


@pytest.mark.parametrize("expression", EXPRESSIONS)
def test_ply_matches_upstream(upstream_ply, expression):
    reference_lex, reference_yacc = upstream_ply
    ours = lex.lex(module=spec)
    reference = reference_lex.lex(module=spec)
    assert tokens(ours, expression) == tokens(reference, expression)
    parser = yacc.yacc(module=spec)
    reference_parser = reference_yacc.yacc(module=spec, debug=False)
    assert parser.parse(expression, lexer=ours) == reference_parser.parse(expression, lexer=reference)


def test_ply_errors_match_upstream(upstream_ply):
    reference_lex, reference_yacc = upstream_ply
    for lexer_api, parser_api in [(lex, yacc), (reference_lex, reference_yacc)]:
        lexer = lexer_api.lex(module=spec)
        parser = parser_api.yacc(module=spec, debug=False)
        with pytest.raises(SyntaxError):
            parser.parse("1 + + 2", lexer=lexer)
