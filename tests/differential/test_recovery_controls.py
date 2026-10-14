"""Explicit recovery controls follow each compatibility API's protocol."""

from types import SimpleNamespace

import pytest

from rply import Token
from rply.compat import sly
from rply.compat.ply import yacc

pytestmark = pytest.mark.differential


def test_sly_errok_alone_keeps_grammar_error_recovery(upstream_sly):
    def run(api):
        class Parser(api.Parser):
            tokens = {"N", "BAD", "SEMI"}

            @_("N SEMI")
            def s(self, p):
                return "normal"

            @_("N error SEMI")
            def s(self, p):
                return "recovered"

            def error(self, tok):
                self.errors.append(None if tok is None else tok.type)
                self.errok()

        parser = Parser()
        parser.errors = []
        if hasattr(parser, "_lrtable"):
            parser._lrtable.defaulted_states.clear()
        result = parser.parse(iter([Token("N", 1), Token("BAD", "?"), Token("SEMI", ";")]))
        return result, parser.errors

    assert run(sly) == run(upstream_sly) == ("recovered", ["BAD", "BAD"])


def test_ply_restart_without_errok_ignores_returned_token(upstream_ply):
    def run(api):
        errors = []

        def p_s(p):
            "s : N SEMI"
            p[0] = p[1]

        def p_error(tok):
            errors.append(None if tok is None else tok.type)
            assert parser.token().type == "SEMI"
            parser.restart()
            return Token("N", "replacement")

        module = {"tokens": ("N", "BAD", "SEMI"), "p_s": p_s, "p_error": p_error}
        if api is yacc:
            parser = api.yacc(module=module)
        else:
            parser = api.yacc(module=SimpleNamespace(**module, __file__=__file__),
                              debug=False)
            parser.disable_defaulted_states()
        tokens = iter([Token("N", "discarded"), Token("BAD", "?"), Token("SEMI", ";"),
                       Token("N", "actual"), Token("SEMI", ";")])
        if api is yacc:
            result = parser.parse(tokenfunc=lambda: next(tokens, None))
        else:
            result = parser.parse(lexer=SimpleNamespace(token=lambda: next(tokens, None)))
        return result, errors

    assert run(yacc) == run(upstream_ply[1]) == ("actual", ["BAD"])
