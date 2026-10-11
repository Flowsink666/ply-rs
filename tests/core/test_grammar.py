"""
Unit tests for core Grammar and FIRST/FOLLOW calculation.
"""

import pytest
from rply.core.grammar import Grammar, Production, GrammarError
from rply.core.symbols import EOF_SYMBOL, START_SYMBOL


def test_grammar_first_follow():
    # Grammar:
    # E -> T E'
    # E' -> PLUS T E' | epsilon
    # T -> F T'
    # T' -> TIMES F T' | epsilon
    # F -> LPAREN E RPAREN | ID
    g = Grammar(terminals={"PLUS", "TIMES", "LPAREN", "RPAREN", "ID"})

    g.add_production("E", ["T", "E_prime"])
    g.add_production("E_prime", ["PLUS", "T", "E_prime"])
    g.add_production("E_prime", [])  # epsilon
    g.add_production("T", ["F", "T_prime"])
    g.add_production("T_prime", ["TIMES", "F", "T_prime"])
    g.add_production("T_prime", [])  # epsilon
    g.add_production("F", ["LPAREN", "E", "RPAREN"])
    g.add_production("F", ["ID"])

    g.set_start("E")

    first = g.compute_first()
    assert "ID" in first["F"]
    assert "LPAREN" in first["F"]
    assert "PLUS" in first["E_prime"]
    assert "" in first["E_prime"]  # epsilon

    follow = g.compute_follow()
    assert EOF_SYMBOL in follow["E"]
    assert "RPAREN" in follow["E"]
    assert "PLUS" in follow["T"]
    assert EOF_SYMBOL in follow["T"]


def test_grammar_validation_unknown_symbol():
    g = Grammar(terminals={"A"})
    g.add_production("S", ["A", "UNKNOWN"])
    g.set_start("S")

    warnings = g.validate()
    assert len(warnings) == 1
    assert "UNKNOWN" in warnings[0]
