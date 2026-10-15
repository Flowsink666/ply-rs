"""Precedence levels, nonassociativity, and literal fallback."""

import pytest

from ply_rs import Grammar, LRParser, LRTable, Token


@pytest.mark.parametrize("reverse", [False, True])
def test_nonassoc_remains_forbidden_after_repeated_shift_entries(reverse):
    grammar = Grammar({"N", "LT", "PLUS"})
    grammar.set_precedence([("nonassoc", ["LT"]), ("left", ["PLUS"])])
    rhs_rules = [["e", "LT", "e"], ["e", "LT", "e", "PLUS", "e"]]
    for rhs in reversed(rhs_rules) if reverse else rhs_rules:
        grammar.add_production("e", rhs)
    grammar.add_production("e", ["N"])
    grammar.set_start("e")
    parser = LRParser(LRTable(grammar))
    assert parser.parse([Token("N", 1), Token("LT", "<"), Token("N", 2)]) == 1
    with pytest.raises(SyntaxError):
        parser.parse([
            Token("N", 1), Token("LT", "<"), Token("N", 2),
            Token("LT", "<"), Token("N", 3),
        ])


def test_rightmost_terminal_with_no_precedence_ends_search():
    grammar = Grammar({"A", "B"})
    grammar.set_precedence([("left", ["A"])])
    grammar.add_production("s", ["A", "B", "tail"])
    grammar.add_production("tail", [])
    grammar.set_start("s")
    assert grammar.Productions[1].prec == ("right", 0)
    assert LRParser(LRTable(grammar)).parse([Token("A", 1), Token("B", 2)]) == 1


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
