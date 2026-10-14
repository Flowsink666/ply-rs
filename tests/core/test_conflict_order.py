"""Mixed conflicts follow the reference closure order in both compilers."""

import importlib
import importlib.util
from types import SimpleNamespace

import pytest

from ply_rs import Grammar, LRParser, LRTable, Token
from tests.core.test_backends import run_python


RULES = (
    ("A", (), None),
    ("B", (), "HIGH"),
    ("s", ("A", "a"), None),
    ("s", ("B", "a"), None),
    ("s", ("a", "b"), None),
)
PRECEDENCE = (("left", ("a",)), ("right", ("HIGH",)))
EXPECTED = (4, ((2, ()), "a"))


def make_conflict_grammar():
    grammar = Grammar({"a", "b", "HIGH"})
    grammar.set_precedence([(assoc, list(tokens)) for assoc, tokens in PRECEDENCE])
    for line, (name, rhs, precedence) in enumerate(RULES, 1):
        def action(*values, rule=line):
            return rule, values

        grammar.add_production(
            name, list(rhs), action, line=line, precedence=precedence,
            action_style="values",
        )
    grammar.set_start("s")
    assert grammar.Productions[1].prec == ("right", 0)
    assert grammar.Productions[2].prec == ("right", 2)
    return grammar


@pytest.mark.parametrize("frozen", [False, True])
def test_two_reductions_and_shift_keep_reference_choice(frozen):
    table = LRTable(make_conflict_grammar())
    # Closure visits the shift before A's and B's empty reductions. The shift
    # beats A (level 0), then B (level 2) beats the shift (level 1).
    assert table.action[0]["a"] == -2
    parser = LRParser(table, frozen=frozen)
    assert parser.parse([Token("a", "a")]) == EXPECTED
    with pytest.raises(SyntaxError):
        parser.parse([Token("a", "a"), Token("b", "b")])
    assert parser.parse([Token("a", "a")]) == EXPECTED


@pytest.mark.parametrize("mode", ["rust", "auto"])
def test_mixed_conflict_backend_selection(mode):
    if mode != "python" and importlib.util.find_spec("ply_rs._core_rs") is None:
        pytest.skip("native extension unavailable")
    result = run_python(mode, r'''
import os
from ply_rs import LRParser, LRTable, Token
from tests.core.test_conflict_order import make_conflict_grammar, EXPECTED

table = LRTable(make_conflict_grammar())
assert table.backend == 'rust'
assert table.action[0]['a'] == -2, table.action[0]
assert LRParser(table).parse([Token('a', 'a')]) == EXPECTED
assert [(c.kind, c.token, c.resolution) for c in table.conflicts if c.state == 0] == [
    ('shift/reduce', 'a', 'shift'), ('shift/reduce', 'a', 'reduce'),
]
''')
    assert result.returncode == 0, result.stderr


@pytest.mark.differential
def test_independent_pinned_ply_agrees_on_mixed_conflict():
    # Reuse the optional/strict reference loader from the differential suite.
    from tests.differential.conftest import load_reference

    package = load_reference("ply")
    yacc = importlib.import_module(f"{package.__name__}.yacc")
    grammar = yacc.Grammar(["a", "b", "HIGH"])
    for level, (assoc, tokens) in enumerate(PRECEDENCE, 1):
        for token in tokens:
            grammar.set_precedence(token, assoc, level)
    actions = {}
    for line, (name, rhs, precedence) in enumerate(RULES, 1):
        action_name = f"rule_{line}"

        def action(proxy, rule=line):
            proxy[0] = rule, tuple(proxy[1:])

        actions[action_name] = action
        symbols = list(rhs) + (["%prec", precedence] if precedence else [])
        grammar.add_production(name, symbols, func=action_name, line=line)
    grammar.set_start("s")
    reference_table = yacc.LRTable(grammar)
    reference_table.bind_callables(actions)

    def reject(token):
        raise SyntaxError("reference parser rejected input")

    reference = yacc.LRParser(reference_table, reject)
    reference.disable_defaulted_states()
    source = iter([Token("a", "a")])
    assert reference.parse(lexer=SimpleNamespace(token=lambda: next(source, None))) == EXPECTED
    table = LRTable(make_conflict_grammar())
    assert table.action[0]["a"] == reference_table.lr_action[0]["a"] == -2
    assert LRParser(table).parse([Token("a", "a")]) == EXPECTED
