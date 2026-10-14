"""Lookahead merging must preserve tables, public lists, and diagnostics."""

import pytest

from rply.backends.python.lr_table import PythonLRTable
from rply.backends.python.parser import PythonLRParser
from rply.core.diagnostics import DiagnosticsFormatter
from rply.core.grammar import Grammar
from rply.core.symbols import EOF_SYMBOL
from rply.core.table_data import ParseTableData
from rply.core.tokens import Token


def state_keys(table):
    """Identify states by their items, independent of set iteration order."""
    return {
        state: tuple(sorted((item.production.number, item.lr_index) for item in items))
        for state, items in enumerate(table.states)
    }


def table_rows(table):
    keys = state_keys(table)
    return {
        keys[state]: (
            {
                token: ("shift", keys[action]) if action is not None and action > 0 else action
                for token, action in table.action[state].items()
            },
            {name: keys[target] for name, target in table.goto[state].items()},
        )
        for state in table.action
    }


@pytest.mark.parametrize("width", [2, 16])
def test_shared_reduction_merges_overlapping_lookbacks_in_order(width):
    prefixes = [f"PREFIX{i}" for i in range(width)]
    suffixes = [f"SUFFIX{i}" for i in range(16)]
    grammar = Grammar(set(prefixes + suffixes + ["VALUE"]))
    for prefix in prefixes:
        for suffix in suffixes:
            grammar.add_production(
                "s", [prefix, "item", suffix],
                lambda left, value, right: (left, value, right), action_style="values",
            )
    grammar.add_production("item", ["VALUE"], lambda value: value, action_style="values")
    grammar.set_start("s")
    table = PythonLRTable(grammar)

    # Every prefix reaches the same item -> VALUE reduction. Its lookbacks
    # contribute the same suffixes repeatedly, in grammar declaration order.
    production = grammar.Productions[-1]
    assert len(production.lookaheads) == 1
    reduction_state, lookaheads = next(iter(production.lookaheads.items()))
    assert isinstance(lookaheads, list)
    assert lookaheads == suffixes
    assert table.action[reduction_state] == {
        suffix: -production.number for suffix in suffixes
    }
    for prefix in prefixes:
        prefix_state = table.action[0][prefix]
        assert table.action[prefix_state]["VALUE"] == reduction_state
    assert table.conflicts == []

    parser = PythonLRParser(ParseTableData.from_table(table))
    for prefix in (prefixes[0], prefixes[-1]):
        for suffix in suffixes:
            assert parser.parse([
                Token(prefix, prefix), Token("VALUE", 7), Token(suffix, suffix),
            ]) == (prefix, 7, suffix)


def conflicting_grammar():
    # This is LR(1), but LALR merges the A -> c and B -> c reductions.
    grammar = Grammar({"a", "b", "c", "d", "e"})
    for name, rhs in [
        ("S", "a A d"), ("S", "b A e"),
        ("S", "a B e"), ("S", "b B d"),
        ("A", "c"), ("B", "c"),
    ]:
        grammar.add_production(name, rhs.split())
    grammar.set_start("S")
    return grammar


def test_lookback_merging_preserves_action_goto_and_conflict_diagnostics():
    grammar = conflicting_grammar()
    table = PythonLRTable(grammar)
    initial = ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0))
    a_state = ((1, 1), (3, 1), (5, 0), (6, 0))
    b_state = ((2, 1), (4, 1), (5, 0), (6, 0))
    reduction = ((5, 1), (6, 1))
    assert table_rows(table) == {
        initial: ({"a": ("shift", a_state), "b": ("shift", b_state)}, {"S": ((0, 1),)}),
        ((0, 1),): ({EOF_SYMBOL: 0}, {}),
        a_state: ({"c": ("shift", reduction)}, {"A": ((1, 2),), "B": ((3, 2),)}),
        b_state: ({"c": ("shift", reduction)}, {"A": ((2, 2),), "B": ((4, 2),)}),
        ((1, 2),): ({"d": ("shift", ((1, 3),))}, {}),
        ((2, 2),): ({"e": ("shift", ((2, 3),))}, {}),
        ((3, 2),): ({"e": ("shift", ((3, 3),))}, {}),
        ((4, 2),): ({"d": ("shift", ((4, 3),))}, {}),
        ((1, 3),): ({EOF_SYMBOL: -1}, {}),
        ((2, 3),): ({EOF_SYMBOL: -2}, {}),
        ((3, 3),): ({EOF_SYMBOL: -3}, {}),
        ((4, 3),): ({EOF_SYMBOL: -4}, {}),
        reduction: ({"d": -5, "e": -5}, {}),
    }

    numbers = {key: state for state, key in state_keys(table).items()}
    reduction_state = numbers[reduction]
    # State creation determines which predecessor contributes its terminal first.
    a_lookaheads = ["d", "e"] if numbers[a_state] < numbers[b_state] else ["e", "d"]
    b_lookaheads = a_lookaheads[::-1]
    assert grammar.Productions[5].lookaheads[reduction_state] == a_lookaheads
    assert grammar.Productions[6].lookaheads[reduction_state] == b_lookaheads
    assert [(c.kind, c.token, c.resolution) for c in table.conflicts] == [
        ("reduce/reduce", token, "reduce using rule 5") for token in b_lookaheads
    ]
    expected = "=== Grammar Analysis: 2 conflict(s) found ===\n\n"
    for index, token in enumerate(b_lookaheads, 1):
        expected += (
            f"[{index}] REDUCE/REDUCE conflict in State {reduction_state} on token {token!r}:\n"
            "    Resolution: REDUCE USING RULE 5\n"
            "    Reason    : Rule (A -> c) was defined earlier than (B -> c)\n"
            "    Candidates:\n"
            "      - Rule 5: A -> c\n"
            "      - Rule 6: B -> c\n\n"
        )
    assert DiagnosticsFormatter.format_conflicts(table) == expected[:-1]

    parser = PythonLRParser(ParseTableData.from_table(table))
    for text in ("acd", "bce"):
        assert parser.parse([Token(char, char) for char in text]) == text[0]
    for text in ("ace", "bcd"):
        with pytest.raises(SyntaxError):
            parser.parse([Token(char, char) for char in text])


def test_rebuilding_grammar_keeps_existing_lookahead_lists():
    grammar = conflicting_grammar()
    first = PythonLRTable(grammar)
    original = [dict(production.lookaheads) for production in grammar.Productions]
    original_values = [
        {state: list(values) for state, values in production.lookaheads.items()}
        for production in grammar.Productions
    ]

    second = PythonLRTable(grammar)
    assert second.action == first.action
    assert second.goto == first.goto
    assert DiagnosticsFormatter.format_conflicts(second) == DiagnosticsFormatter.format_conflicts(first)
    for production, previous, values in zip(grammar.Productions, original, original_values):
        assert production.lookaheads == values
        for state, lookaheads in production.lookaheads.items():
            assert lookaheads is previous[state]
