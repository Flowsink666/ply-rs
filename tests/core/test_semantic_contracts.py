"""Semantic contracts across registration, compilation, and parsing."""

import pytest
from types import SimpleNamespace

from ply_rs import Grammar, LexerEngine, LRParser, LRTable, Token


@pytest.mark.parametrize("resolved_zero_override", [False, True])
def test_resolved_zero_precedence_controls_final_tree(resolved_zero_override):
    grammar = Grammar({"A", "B", "N"})
    grammar.set_precedence([("left", ["A"])])
    rhs = ["e", "A", "e"] if resolved_zero_override else ["e", "A", "B", "e"]
    grammar.add_production("e", rhs, lambda p: (p[1], p[len(rhs)]))
    # Simulate a resolved public precedence supplied to the table compiler.
    if resolved_zero_override:
        grammar.Productions[0].prec = ("right", 0)
    grammar.add_production("e", ["N"])
    grammar.set_start("e")
    assert grammar.Productions[1].prec == ("right", 0)
    parser = LRParser(LRTable(grammar))

    tokens = [Token("N", 1)]
    for value in (2, 3):
        tokens.append(Token("A", "a"))
        if not resolved_zero_override:
            tokens.append(Token("B", "b"))
        tokens.append(Token("N", value))
    assert parser.parse(tokens) == (1, (2, 3))


@pytest.mark.parametrize("redeclare", [False, True])
@pytest.mark.parametrize("frozen", [False, True])
def test_native_conversion_preserves_gaps_in_precedence_levels(redeclare, frozen):
    grammar = Grammar({"N", "A", "B", "C"})
    if redeclare:
        grammar.set_precedence([("left", ["A"]), ("left", ["B"]), ("right", ["C"])])
        grammar.set_precedence([("left", ["B"])])
        assert grammar.Precedence["C"] == ("right", 3)
    else:
        grammar.set_precedence([("left", []), ("right", ["C"])])
        assert grammar.Precedence["C"] == ("right", 2)
    grammar.add_production("e", ["e", "C", "e"], lambda p: (p[1], p[3]))
    grammar.add_production("e", ["N"])
    grammar.set_start("e")
    parser = LRParser(LRTable(grammar), frozen=frozen)
    assert parser.parse([
        Token("N", 1), Token("C", "c"), Token("N", 2), Token("C", "c"), Token("N", 3),
    ]) == (1, (2, 3))


def test_nonassoc_cannot_fall_back_to_same_value_literal():
    grammar = Grammar({"N", "LT"})
    grammar.set_precedence([("nonassoc", ["LT"]), ("left", ["'<'"])])
    grammar.add_production("e", ["e", "LT", "e"], lambda p: ("lt", p[1], p[3]))
    grammar.add_production("e", ["e", "'<'", "e"], lambda p: ("literal", p[1], p[3]))
    grammar.add_production("e", ["N"])
    grammar.set_start("e")
    parser = LRParser(LRTable(grammar))
    assert parser.parse([Token("N", 1), Token("LT", "<"), Token("N", 2)]) == ("lt", 1, 2)
    with pytest.raises(SyntaxError):
        parser.parse([
            Token("N", 1), Token("LT", "<"), Token("N", 2),
            Token("LT", "<"), Token("N", 3),
        ])
    assert parser.parse([
        Token("N", 1), Token("OTHER", "<"), Token("N", 2),
    ]) == ("literal", 1, 2)


@pytest.mark.parametrize("forbid_type", [False, True])
def test_explicit_none_blocks_literal_fallback_but_missing_type_allows_it(forbid_type):
    grammar = Grammar({"NAMED"})
    grammar.add_production("s", ["'x'"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    if forbid_type:
        parser.action[0]["NAMED"] = None
        with pytest.raises(SyntaxError):
            parser.parse([Token("NAMED", "x")])
    else:
        assert parser.parse([Token("NAMED", "x")]) == "x"


@pytest.mark.parametrize("has_valid_sync_token", [False, True])
def test_recovery_uses_same_forbidden_and_literal_lookup(has_valid_sync_token):
    grammar = Grammar({"BAD", "TRIGGER"})
    grammar.add_production("s", ["error", "';'"], lambda p: p.lexspan(2))
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    error_state = parser.action[0]["error"]
    parser.action[error_state]["BAD"] = None
    tokens = [Token("TRIGGER", "!", lexpos=0), Token("BAD", ";", lexpos=1)]
    if has_valid_sync_token:
        tokens.append(Token("OTHER", ";", lexpos=2))
        assert parser.parse(tokens) == (2, 3)
    else:
        with pytest.raises(SyntaxError):
            parser.parse(tokens)


@pytest.mark.parametrize("invalid_error_action", [None, 0, -1, -999, -(1 << 31)])
def test_recovery_searches_for_actual_error_shift(invalid_error_action):
    grammar = Grammar({"PREFIX", "SEMI", "BAD"})
    grammar.add_production("s", ["PREFIX", "SEMI"], lambda p: "normal")
    grammar.add_production("s", ["error", "SEMI"], lambda p: "recovered")
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    prefix_state = parser.action[0]["PREFIX"]
    parser.action[prefix_state]["error"] = invalid_error_action
    assert parser.parse([
        Token("PREFIX", "prefix"), Token("BAD", "?"), Token("SEMI", ";"),
    ]) == "recovered"


def test_recovery_can_resume_with_eof_reduction():
    grammar = Grammar({"BAD"})
    grammar.add_production("s", ["error"], lambda p: p[1])
    grammar.set_start("s")
    assert LRParser(LRTable(grammar)).parse([Token("BAD", "?")]) == "?"


class FalseyCallback:
    def __init__(self, callback):
        self.callback = callback

    def __bool__(self):
        return False

    def __call__(self, *args):
        return self.callback(*args)


@pytest.mark.parametrize("attribute", ["type", "value", "lineno", "lexpos", "index", "end"])
@pytest.mark.parametrize("exception_type", [RuntimeError, StopIteration])
def test_token_property_exceptions_propagate_without_running_actions(attribute, exception_type):
    class ExplodingToken:
        type = "N"
        value = 1
        lineno = 1
        lexpos = 0
        end = 1

        def __getattribute__(self, name):
            if attribute == "index" and name == "lexpos":
                raise AttributeError(name)
            if name == attribute:
                raise exception_type(f"cannot read {attribute}")
            return super().__getattribute__(name)

        def __str__(self):
            return "N"

    calls = []
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], lambda p: calls.append(p[1]))
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    with pytest.raises(exception_type, match=f"cannot read {attribute}"):
        parser.parse([ExplodingToken()])
    assert calls == []


@pytest.mark.parametrize("positions, expected", [
    ({}, ((1, 1), (0, 3))),
    ({"index": 9}, ((1, 1), (9, 12))),
    ({"lineno": 3, "lexpos": 2, "index": 999}, ((3, 3), (2, 5))),
    ({"lineno": 4, "lexpos": 2, "end": 17}, ((4, 4), (2, 17))),
])
def test_missing_token_positions_use_defaults_and_index_fallback(positions, expected):
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], lambda p: (p.linespan(1), p.lexspan(1)))
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    assert parser.parse([SimpleNamespace(type="N", value=123, **positions)]) == expected


@pytest.mark.parametrize("attribute", ["lineno", "lexpos", "index", "end"])
@pytest.mark.parametrize("exception_type", [RuntimeError, StopIteration])
def test_native_position_conversion_exceptions_propagate(attribute, exception_type):
    class ExplodingInteger:
        def __index__(self):
            raise exception_type(f"cannot convert {attribute}")

    positions = {"lineno": 1, "lexpos": 0, "end": 1}
    if attribute == "index":
        del positions["lexpos"]
    positions[attribute] = ExplodingInteger()
    calls = []
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], lambda p: calls.append(p[1]))
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    with pytest.raises(exception_type, match=f"cannot convert {attribute}"):
        parser.parse([SimpleNamespace(type="N", value=1, **positions)])
    assert calls == []


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("exception_type", [RuntimeError, StopIteration])
def test_lazy_error_value_exceptions_propagate_before_reduction(frozen, exception_type):
    class ExplodingText(str):
        def __getitem__(self, key):
            if isinstance(key, slice):
                raise exception_type("cannot materialize error text")
            return super().__getitem__(key)

    error_calls, action_calls = [], []

    def recover(tok):
        error_calls.append(tok.lexpos)
        tok.type = "N"
        tok.lexer.skip()
        return tok

    lexer = LexerEngine()
    lexer.set_error_func("INITIAL", recover)
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], lambda p: action_calls.append(p[1]))
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar), frozen=frozen)
    with pytest.raises(exception_type, match="cannot materialize error text"):
        parser.parse(lexer.tokenize(ExplodingText("?")))
    assert error_calls == [0]
    assert action_calls == []
    assert (lexer.data, lexer.pos, lexer.length) == ("?", 1, 1)


@pytest.mark.parametrize("action_style", ["proxy", "values"])
def test_falsey_production_and_error_callbacks_are_called(action_style):
    grammar = Grammar({"N", "BAD"})
    action = (lambda p: p[1] + 1) if action_style == "proxy" else (lambda value: value + 1)
    grammar.add_production("s", ["N"], FalseyCallback(action), action_style=action_style)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar), error_func=FalseyCallback(lambda token: ("error", token.value)))
    assert parser.parse([Token("N", 1)]) == 2
    assert parser.parse([Token("BAD", "?")]) == ("error", "?")
