"""
Unit tests for LALR(1) table construction and conflict resolution.
"""

import pytest
from rply.core.grammar import Grammar
from rply.core.lr_table import LRTable
from rply.core.symbols import EOF_SYMBOL


def test_lr_table_simple_grammar():
    # S -> A B
    # A -> 'a'
    # B -> 'b'
    g = Grammar(terminals={"a", "b"})
    g.add_production("S", ["A", "B"])
    g.add_production("A", ["a"])
    g.add_production("B", ["b"])
    g.set_start("S")

    table = LRTable(g)
    assert len(table.states) > 0
    assert len(table.conflicts) == 0

    # Start state should have shift on 'a'
    assert table.action[0]["a"] is not None
    assert table.action[0]["a"] > 0


def test_lr_table_operator_precedence():
    # expr -> expr PLUS expr
    # expr -> expr TIMES expr
    # expr -> NUMBER
    g = Grammar(terminals={"PLUS", "TIMES", "NUMBER"})
    g.set_precedence([
        ("left", ["PLUS"]),
        ("left", ["TIMES"]),
    ])

    g.add_production("expr", ["expr", "PLUS", "expr"])
    g.add_production("expr", ["expr", "TIMES", "expr"])
    g.add_production("expr", ["NUMBER"])
    g.set_start("expr")

    table = LRTable(g)
    # S/R conflicts were resolved by precedence
    assert len(table.conflicts) > 0
    for c in table.conflicts:
        assert c.kind == "shift/reduce"
        assert c.resolution in ("shift", "reduce")
