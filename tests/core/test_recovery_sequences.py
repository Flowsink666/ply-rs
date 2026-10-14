"""Recovery must preserve successive statements while suppressing callbacks."""

import pytest

from rply import Grammar, LRParser, LRTable, Token


def statement_parser(errors, *, nested=False, frozen=False):
    grammar = Grammar({"START", "ID", "SEMI", "BAD", "LBRACE", "RBRACE"})
    grammar.add_production("s", ["START", "stmts"], lambda p: p[2])
    grammar.add_production("stmts", ["stmts", "stmt"], lambda p: p[1] + [p[2]])
    grammar.add_production("stmts", ["stmt"], lambda p: [p[1]])
    grammar.add_production("stmt", ["ID", "SEMI"], lambda p: ("id", p[1]))
    grammar.add_production("stmt", ["error", "SEMI"], lambda p: ("error", p[1]))
    if nested:
        grammar.add_production(
            "stmt", ["LBRACE", "stmts", "RBRACE", "SEMI"],
            lambda p: ("block", p[2]),
        )
    grammar.set_start("s")
    return LRParser(LRTable(grammar), error_func=lambda token: errors.append(token.value), frozen=frozen)


def tokens(types):
    return [Token(kind, index) for index, kind in enumerate(types)]


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("types, expected, callback_values", [
    (("START", "ID", "SEMI"), [("id", 1)], []),
    (("START", "BAD", "SEMI", "BAD", "SEMI"),
     [("error", 1), ("error", 3)], [1]),
    (("START", "BAD", "SEMI", "ID", "SEMI", "BAD", "SEMI"),
     [("error", 1), ("id", 3), ("error", 5)], [1, 5]),
    (("START", "BAD", "SEMI", "BAD", "SEMI", "ID", "SEMI", "BAD", "SEMI"),
     [("error", 1), ("error", 3), ("id", 5), ("error", 7)], [1, 7]),
    (("START", "BAD", "BAD", "BAD", "SEMI"), [("error", 1)], [1]),
    # The synthetic error shift does not count towards three real input shifts.
    (("START", "SEMI", "ID", "ID", "SEMI"),
     [("error", 1), ("error", 3)], [1]),
])
def test_successive_recoveries_preserve_statements(types, expected, callback_values, frozen):
    errors = []
    parser = statement_parser(errors, frozen=frozen)
    assert parser.parse(tokens(types)) == expected
    assert errors == callback_values


def test_successive_errors_inside_nested_statement():
    errors = []
    parser = statement_parser(errors, nested=True)
    assert parser.parse(tokens((
        "START", "LBRACE", "BAD", "SEMI", "BAD", "SEMI", "ID", "SEMI",
        "RBRACE", "SEMI", "BAD", "SEMI",
    ))) == [
        ("block", [("error", 2), ("error", 4), ("id", 6)]),
        ("error", 10),
    ]
    assert errors == [2, 10]


@pytest.mark.parametrize("types", [
    ("START", "BAD"),
    ("START", "BAD", "SEMI", "BAD"),
    ("START", "BAD", "SEMI", "ID"),
])
def test_missing_synchronization_token_fails_at_eof_without_refetching(types):
    errors = []
    parser = statement_parser(errors)
    source = iter(tokens(types))
    fetches = []

    class OneShotEOF:
        def __iter__(self):
            return self

        def __next__(self):
            fetches.append(None)
            assert len(fetches) <= len(types) + 1, "parser requested EOF repeatedly"
            return next(source)

    with pytest.raises(SyntaxError):
        parser.parse(OneShotEOF())
    assert len(fetches) == len(types) + 1
    assert errors == [1]


def test_nonassoc_expression_recovers_then_preserves_following_statement():
    grammar = Grammar({"START", "N", "LT", "SEMI"})
    grammar.set_precedence([("nonassoc", ["LT"]), ("left", ["'<'"])])
    grammar.add_production("s", ["START", "stmts"], lambda p: p[2])
    grammar.add_production("stmts", ["stmts", "stmt"], lambda p: p[1] + [p[2]])
    grammar.add_production("stmts", ["stmt"], lambda p: [p[1]])
    grammar.add_production("stmt", ["e", "SEMI"], lambda p: p[1])
    grammar.add_production("stmt", ["error", "SEMI"], lambda p: ("error", p[1]))
    grammar.add_production("e", ["e", "LT", "e"], lambda p: ("lt", p[1], p[3]))
    grammar.add_production("e", ["e", "'<'", "e"], lambda p: ("literal", p[1], p[3]))
    grammar.add_production("e", ["N"])
    grammar.set_start("s")
    errors = []
    parser = LRParser(LRTable(grammar), error_func=lambda token: errors.append(token.type))
    assert parser.parse([
        Token("START", "start"), Token("N", 1), Token("LT", "<"),
        Token("N", 2), Token("LT", "<"), Token("N", 3), Token("SEMI", ";"),
        Token("N", 4), Token("SEMI", ";"),
    ]) == [("error", "<"), 4]
    assert errors == ["LT"]


def test_synthetic_error_does_not_use_original_value_as_literal():
    grammar = Grammar({"PREFIX", "BAD"})
    grammar.add_production("s", ["PREFIX", "'x'"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    parser.action[parser.action[0]["PREFIX"]]["BAD"] = None
    with pytest.raises(SyntaxError):
        parser.parse([Token("PREFIX", "prefix"), Token("BAD", "x")])
