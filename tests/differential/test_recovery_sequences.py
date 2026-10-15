"""Recovery boundaries and independently built pinned PLY tables."""

import pytest

from ply_rs import Grammar, LRParser, LRTable
from tests.fixtures.recovery import (STATEMENT_RULES, _grammar, _independent_oracle, _run_ply, _run_ply_rs)

pytestmark = pytest.mark.differential


@pytest.mark.parametrize("frozen", [False, True], ids=["dynamic", "frozen"])
def test_recovery_boundaries_match_independent_tables(upstream_ply, frozen):
    terminals = {"START", "ID", "SEMI", "BAD"}
    errors, reference_errors = [], []
    parser = LRParser(LRTable(_grammar(terminals, STATEMENT_RULES, "s")),
                      error_func=lambda token: errors.append(token.value), frozen=frozen)
    reference = _independent_oracle(upstream_ply[1], terminals, STATEMENT_RULES, "s", reference_errors)
    # Explicit input/output boundaries; no counts tied to the size of a search.
    cases = (
        ("", (False, None), ["$end"], ["$end"]),
        ("ID", (False, None), ["$end"], ["$end"]),
        ("ID SEMI", (True, [("id", 1)]), [], []),
        ("BAD", (False, None), [1], [1]),
        ("BAD SEMI", (True, [("error", 1)]), [1], [1]),
        ("SEMI", (True, [("error", 1)]), [1], [1]),
        ("ID BAD SEMI", (True, [("error", 2)]), [2], [2]),
        ("ID ID SEMI", (True, [("error", 2)]), [2], [2]),
        ("BAD BAD SEMI", (True, [("error", 1)]), [1], [1]),
        ("ID SEMI BAD SEMI", (True, [("id", 1), ("error", 3)]), [3], [3]),
        ("BAD SEMI BAD SEMI", (True, [("error", 1), ("error", 3)]), [1], [1]),
        ("BAD SEMI ID SEMI BAD SEMI", (True, [("error", 1), ("id", 3), ("error", 5)]), [1, 5], [1, 5]),
        ("ID SEMI ID SEMI BAD SEMI", (True, [("id", 1), ("id", 3), ("error", 5)]), [5], [5]),
        ("ID SEMI BAD", (False, None), [3], [3]),
        ("BAD SEMI ID", (False, None), [1], [1]),
        ("ID SEMI SEMI ID SEMI", (True, [("id", 1), ("error", 3), ("id", 4)]), [3], [3]),
        # PLY counts the synthetic error shift; core requires three real shifts.
        ("SEMI ID ID SEMI", (True, [("error", 1), ("error", 3)]), [1], [1, 3]),
    )
    for suffix, expected, expected_errors, expected_reference_errors in cases:
        types = ("START",) + tuple(suffix.split())
        entries = tuple((kind, index) for index, kind in enumerate(types))
        errors.clear()
        reference_errors.clear()
        assert _run_ply_rs(parser, entries) == expected, suffix
        assert _run_ply(reference, entries) == expected, suffix
        assert errors == expected_errors, suffix
        assert reference_errors == expected_reference_errors, suffix


@pytest.mark.parametrize("frozen", [False, True], ids=["dynamic", "frozen"])
@pytest.mark.parametrize("redeclare", [False, True], ids=["empty-tier", "redeclared-tier"])
def test_independent_precedence_tables_keep_numeric_gaps(upstream_ply, frozen, redeclare):
    rules = (
        ("e", ("e", "C", "e"), lambda p: (p[1], p[3])),
        ("e", ("N",), lambda p: p[1]),
    )
    grammar = Grammar({"N", "A", "B", "C"})
    if redeclare:
        grammar.set_precedence([("left", ["A"]), ("left", ["B"]), ("right", ["C"])])
        grammar.set_precedence([("left", ["B"])])
        expected_precedence = {"A": ("left", 1), "B": ("left", 1), "C": ("right", 3)}
    else:
        grammar.set_precedence([("left", []), ("right", ["C"])])
        expected_precedence = {"C": ("right", 2)}
    assert grammar.Precedence == expected_precedence
    for name, rhs, action in rules:
        grammar.add_production(name, list(rhs), action)
    grammar.set_start("e")
    parser = LRParser(LRTable(grammar), frozen=frozen)
    # PLY rejects repeated declarations. Supply the independently specified
    # final numeric mapping rather than deriving it from ply-rs's table/adapter.
    reference = _independent_oracle(upstream_ply[1], {"N", "A", "B", "C"}, rules,
                                    "e", [], expected_precedence.items())
    entries = (("N", 1), ("C", "c"), ("N", 2), ("C", "c"), ("N", 3))
    expected = True, (1, (2, 3))
    assert _run_ply_rs(parser, entries) == expected == _run_ply(reference, entries)


def test_precedence_redeclaration_policies_are_explicit(upstream_ply):
    grammar = Grammar({"C"})
    grammar.set_precedence([("left", ["C"])])
    grammar.set_precedence([("left", []), ("right", ["C"])])
    assert grammar.Precedence == {"C": ("right", 2)}

    yacc = upstream_ply[1]
    reference = yacc.Grammar(["C"])
    reference.set_precedence("C", "left", 1)
    with pytest.raises(yacc.GrammarError, match="Precedence already specified"):
        reference.set_precedence("C", "right", 2)
    assert reference.Precedence == {"C": ("left", 1)}


@pytest.mark.parametrize("frozen", [False, True], ids=["dynamic", "frozen"])
def test_independent_nonassoc_tables_recover_and_keep_following_statement(upstream_ply, frozen):
    rules = (
        ("s", ("START", "stmts"), lambda p: p[2]),
        ("stmts", ("stmts", "stmt"), lambda p: p[1] + [p[2]]),
        ("stmts", ("stmt",), lambda p: [p[1]]),
        ("stmt", ("e", "SEMI"), lambda p: p[1]),
        ("stmt", ("error", "SEMI"), lambda p: ("error", p[1])),
        ("e", ("e", "LT", "e"), lambda p: ("lt", p[1], p[3])),
        ("e", ("N",), lambda p: p[1]),
    )
    terminals = {"START", "N", "LT", "SEMI"}
    errors, reference_errors = [], []
    parser = LRParser(LRTable(_grammar(terminals, rules, "s", [("nonassoc", ["LT"])])),
                      error_func=lambda token: errors.append(token.value), frozen=frozen)
    reference = _independent_oracle(upstream_ply[1], terminals, rules, "s", reference_errors,
                                    [("LT", ("nonassoc", 1))])
    entries = (
        ("START", "start"), ("N", 1), ("LT", "<"), ("N", 2),
        ("LT", "<"), ("N", 3), ("SEMI", ";"), ("N", 4), ("SEMI", ";"),
    )
    expected = True, [("error", "<"), 4]
    assert _run_ply_rs(parser, entries) == expected == _run_ply(reference, entries)
    assert errors == reference_errors == ["<"]
