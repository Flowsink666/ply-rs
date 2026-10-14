"""
Unit tests for rich diagnostics formatting.
"""

from ply_rs.core.grammar import Grammar
from ply_rs.core.lr_table import LRTable
from ply_rs.core.diagnostics import DiagnosticsFormatter


def test_diagnostics_formatting():
    # Ambiguous dangling-else grammar
    g = Grammar(terminals={"IF", "THEN", "ELSE", "OTHER"})
    g.add_production("stmt", ["IF", "stmt", "ELSE", "stmt"])
    g.add_production("stmt", ["IF", "stmt"])
    g.add_production("stmt", ["OTHER"])
    g.set_start("stmt")

    table = LRTable(g)
    report = DiagnosticsFormatter.format_conflicts(table)

    assert "conflict(s) found" in report
    assert "SHIFT/REDUCE" in report
    assert "ELSE" in report

    summary = DiagnosticsFormatter.format_grammar_summary(g, table)
    assert "Grammar Summary:" in summary
    assert "LR(0) States:" in summary
