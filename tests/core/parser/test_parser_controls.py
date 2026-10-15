"""Recovery controls, cursors, and recursive parse contexts."""

from types import SimpleNamespace

import pytest

from ply_rs import Grammar, LRParser, LRTable, Token


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


def test_parse_context_survives_recursive_parse_and_does_not_leak():
    grammar = Grammar({"N"})

    def action(p):
        if p[1] == 2:
            inner = parser.parse([Token("N", 1)], lexer=inner_lexer, parser=inner_owner)
            return p.parser.name, p.lexer.name, inner
        return p.parser.name, p.lexer.name if p.lexer is not None else None

    grammar.add_production("s", ["N"], action)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    parser.name = "default"
    outer_owner = SimpleNamespace(name="outer")
    inner_owner = SimpleNamespace(name="inner")
    outer_lexer = SimpleNamespace(name="outer-lexer")
    inner_lexer = SimpleNamespace(name="inner-lexer")
    assert parser.parse([Token("N", 2)], lexer=outer_lexer, parser=outer_owner) == (
        "outer", "outer-lexer", ("inner", "inner-lexer"),
    )
    assert parser.parse([Token("N", 1)]) == ("default", None)
