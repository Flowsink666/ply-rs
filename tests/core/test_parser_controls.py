"""Recovery controls share a cursor and remain local to each invocation."""

import pytest

from ply_rs import Grammar, LRParser, LRTable, Token
from ply_rs.core.production import ParserError
from ply_rs.compat.ply import lex, yacc
from ply_rs.compat import sly


def test_core_errok_and_recursive_parse_use_independent_sessions():
    grammar = Grammar({"N", "BAD"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    trace = []

    def error(tok):
        trace.append((tok.value, len(parser._sessions)))
        if tok.value == "outer":
            assert parser.parse([Token("BAD", "inner"), Token("N", 2)]) == 2
        parser.errok()
        return parser.token()

    parser = LRParser(LRTable(grammar), error_func=error)
    assert parser.parse([Token("BAD", "outer"), Token("N", 7)]) == 7
    assert trace == [("outer", 1), ("inner", 2)]
    assert parser._sessions == []
    with pytest.raises(RuntimeError, match="active parse"):
        parser.errok()


def test_ply_panic_mode_restart_discards_incomplete_stack():
    lexer = lex.lex(module={"tokens": ("N", "BAD", "SEMI"), "t_N": r"\d+",
                            "t_BAD": r"\?", "t_SEMI": ";", "t_ignore": " "})
    errors = []

    def p_s(p):
        "s : N SEMI"
        p[0] = p[1]

    def p_error(tok):
        assert tok.lexer is lexer
        errors.append(tok.type)
        while True:
            token = parser.token()
            if token is None or token.type == "SEMI":
                break
        parser.restart()

    parser = yacc.yacc(module={"tokens": ("N", "BAD", "SEMI"),
                               "p_s": p_s, "p_error": p_error})
    assert parser.parse("1 ? ; 2 ;", lexer=lexer) == "2"
    assert errors == ["BAD"]


def test_sly_replacement_tokens_and_eof_follow_recovery_protocol():
    class Parser(sly.Parser):
        tokens = {"N", "BAD"}

        @_("N")
        def s(self, p):
            return p[0]

        def error(self, tok):
            self.errors.append(None if tok is None else tok.type)
            if tok is not None:
                self.errok()
                return next(self.tokens, None)

    parser = Parser()
    parser.errors = []
    assert parser.parse([Token("BAD", "?"), Token("N", 7)]) == 7
    assert parser.parse([]) is None
    assert parser.errors == ["BAD", None]
    assert parser.tokens == {"N", "BAD"}


def test_sly_mro_overrides_rules_without_duplicate_diamond_productions():
    class Base(sly.Parser):
        tokens = {"N"}

        @_("N")
        def s(self, p):
            return "base"

    class Left(Base):
        pass

    class Right(Base):
        @_("N")
        def s(self, p):
            return p[0] + 1

    class Child(Left, Right):
        pass

    parser = Child()
    assert parser.parse([Token("N", 7)]) == 8
    assert parser._table.rr_conflicts == []
    assert len(parser._grammar.Productions) == 2


def test_error_handler_exception_releases_recovery_session():
    grammar = Grammar({"N", "BAD"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar), error_func=lambda tok: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        parser.parse([Token("BAD", "?")])
    assert parser._sessions == []
    assert parser.parse([Token("N", 7)]) == 7


def test_recovery_cursor_does_not_fetch_source_again_after_eof():
    from ply_rs.core.parser_context import TokenCursor

    class Source:
        calls = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.calls += 1
            raise StopIteration

    source = Source()
    cursor = TokenCursor(source)
    assert next(cursor, None) is next(cursor, None) is None
    assert source.calls == 1


def test_native_cursor_exposes_iterator_cycles_to_gc():
    import gc
    import weakref
    from ply_rs.core.parser_context import TokenCursor

    class Owner:
        pass

    class Source:
        def __init__(self, owner):
            self.owner = owner

        def __iter__(self):
            return self

        def __next__(self):
            raise StopIteration

    owner = Owner()
    owner.cursor = TokenCursor(Source(owner))
    reference = weakref.ref(owner)
    del owner
    gc.collect()
    assert reference() is None


def test_sly_explicit_bnf_separator_keeps_inherited_productions():
    class Base(sly.Parser):
        tokens = {"N"}

        @_("s ::= N")
        def s(self, p):
            return p[0]

    class Child(Base):
        pass

    assert Child().parse([Token("N", 7)]) == 7


@pytest.mark.parametrize("action", [-999, -(1 << 31), -1])
def test_invalid_reductions_raise_parser_error_without_native_panic(action):
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    parser.action[0]["N"] = action
    with pytest.raises(ParserError, match="Invalid reduction"):
        parser.parse([Token("N", 7)])
    assert parser._sessions == []


@pytest.mark.parametrize("field", ["action", "goto"])
def test_invalid_transition_target_raises_parser_error(field):
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    if field == "action":
        parser.action[0]["N"] = 999
    else:
        parser.goto[0]["s"] = 999
    with pytest.raises(ParserError, match="Invalid .* state"):
        parser.parse([Token("N", 7)])
    assert parser._sessions == []


def test_generic_token_end_overflow_raises_python_exception():
    from types import SimpleNamespace
    from ply_rs.core.lexer_config import MAX_LEXER_COUNTER

    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    with pytest.raises(OverflowError):
        parser.parse([SimpleNamespace(type="N", value="a", lexpos=MAX_LEXER_COUNTER)])
    assert parser._sessions == []
