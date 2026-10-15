"""Long nullable grammars and graph closures retain their public behavior."""

import sys

import pytest

from ply_rs import Grammar, LRParser, LRTable
from ply_rs.core.lr_table import digraph
from tests.fixtures.graphs import closure_by_fixed_point


def test_long_nullable_grammar_builds_and_parses_without_changing_recursion_limit():
    width = 1100
    recursion_limit = sys.getrecursionlimit()
    grammar = Grammar()
    grammar.add_production(
        "s", [f"N{i}" for i in range(width)],
        lambda *values: tuple(values), action_style="values",
    )
    for i in range(width):
        grammar.add_production(
            f"N{i}", [], lambda value=i: value, action_style="values",
        )
    grammar.set_start("s")

    table = LRTable(grammar)
    assert len(table.states) == width + 2
    assert table.conflicts == []
    assert LRParser(table).parse([]) == tuple(range(width))
    assert sys.getrecursionlimit() == recursion_limit


@pytest.mark.parametrize("edges", [
    {0: [1], 1: [2], 2: [0, 3], 3: [3], 4: []},
    {0: [1, 2], 1: [3], 2: [3], 3: [], 4: [5], 5: []},
    {0: [1, 1, 3], 1: [2], 2: [1, 3], 3: [], 4: []},
])
def test_graph_closure_matches_reachable_labels_for_cycles_diamonds_and_components(edges):
    initial = {node: [f"label{node}", "shared"] for node in edges}
    # Successors need not all be named as roots in X.
    actual = digraph([0, 4], lambda node: edges[node], lambda node: initial[node])
    expected = closure_by_fixed_point(edges, initial)
    assert {node: set(labels) for node, labels in actual.items()} == expected


def test_graph_closure_preserves_list_order_and_initial_duplicates_in_cycles():
    edges = {"a": ["b", "c"], "b": ["a", "d"], "c": ["d"], "d": []}
    initial = {"a": ["a", "a"], "b": ["b", "b"], "c": ["c"], "d": ["d"]}
    result = digraph(list(edges), lambda node: edges[node], lambda node: initial[node])
    assert result == {
        "a": ["a", "a", "b", "d", "c"],
        "b": ["a", "a", "b", "d", "c"],
        "c": ["c", "d"],
        "d": ["d"],
    }
    assert result["a"] is result["b"]


def test_graph_closure_handles_a_chain_deeper_than_python_recursion():
    width = 2500
    result = digraph(
        [0],
        lambda node: [node + 1] if node < width - 1 else [],
        lambda node: ["end"] if node == width - 1 else [],
    )
    assert result == {node: ["end"] for node in range(width)}
