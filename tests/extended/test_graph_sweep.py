"""Exhaustive small graphs, run explicitly."""

from ply_rs.core.lr_table import digraph
from tests.fixtures.graphs import closure_by_fixed_point


def test_graph_closure_matches_reachable_labels_for_every_three_node_graph():
    initial = {node: [f"label{node}"] for node in range(3)}
    for mask in range(1 << 9):
        edges = {
            node: [child for child in range(3) if mask & (1 << (node * 3 + child))]
            for node in range(3)
        }
        actual = digraph(list(edges), lambda node: edges[node], lambda node: initial[node])
        assert {node: set(labels) for node, labels in actual.items()} == closure_by_fixed_point(
            edges, initial,
        ), f"edge mask {mask}"
