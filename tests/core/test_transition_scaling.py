"""Wide grammars must not repeatedly encode the same source item sets."""

from benchmarks.workloads import wide_grammar
from rply import Grammar, LRParser, LRTable, Token




def test_wide_grammar_repeated_parse_keeps_reachable_transitions():
    width = 64
    grammar = Grammar({f"CMD{i}" for i in range(width)} | {"VALUE", "END"})
    grammar.add_production("program", [], lambda: [], action_style="values")
    grammar.add_production(
        "program", ["program", "command"],
        lambda program, value: program + [value], action_style="values",
    )
    for i in range(width):
        grammar.add_production(
            "command", [f"CMD{i}", "VALUE", "END"],
            lambda command, value, end: (command, value), action_style="values",
        )
    grammar.set_start("program")
    parser = LRParser(LRTable(grammar))
    tokens = [
        token
        for i in (0, width - 1, width // 2, 0)
        for token in (Token(f"CMD{i}", i), Token("VALUE", i + 1), Token("END", ";"))
    ]
    expected = [(i, i + 1) for i in (0, width - 1, width // 2, 0)]
    assert parser.parse(tokens) == expected
    assert parser.parse(tokens) == expected
    assert parser.freeze().parse(tokens) == expected


def test_shared_transition_preserves_public_lookahead_and_conflict_order(monkeypatch):
    from tests.core.test_backends import run_python

    # State discovery uses symbol sets. Pin their iteration order so this
    # regression checks the ordering produced by the original builder.
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    result = run_python("rust", r'''
from rply import Grammar, LRParser, LRTable

grammar = Grammar({'t0', 't1', 't2'})
rules = [
    ('b', ['t2', 'a', 't0']),
    ('s', ['a']),
    ('a', ['b', 't1', 'b']),
    ('b', ['s', 't0']),
    ('a', ['b']),
    ('a', ['s', 't2']),
    ('a', []),
]
for line, (name, rhs) in enumerate(rules):
    grammar.add_production(name, rhs, line=line)
grammar.set_start('s')
table = LRTable(grammar)
assert table.backend == 'rust'
state = next(
    index for index, items in enumerate(table.states)
    if {(item.production.number, item.lr_index) for item in items}
       >= {(3, 3), (5, 1)}
)
# Two incoming edges reach the same item set with different item order.
# Its canonical state order must not replace the order of each edge's reads.
assert grammar.Productions[5].lookaheads[state] == ['t0', 't2']
assert [record.token for record in table.conflicts
        if record.state == state and record.kind == 'reduce/reduce'] == ['t0', 't2']
assert table.action[state]['t0'] == table.action[state]['t2'] == -3
assert LRParser(table).parse([]) is None
''')
    assert result.returncode == 0, result.stderr


def test_wide_table_exposes_deterministic_unique_item_states():
    workload = wide_grammar(1, 256)
    grammar = Grammar({token.name for token in workload.tokens})
    for rule in workload.rules:
        grammar.add_production(rule.name, rule.rhs.split())
    grammar.set_start(workload.start)
    first, second = LRTable(grammar), LRTable(grammar)
    keys = [tuple((item.production.number, item.lr_index) for item in state) for state in first.states]
    assert len(set(frozenset(key) for key in keys)) == len(keys)
    assert all(len(set(key)) == len(key) for key in keys)
    assert first.action == second.action and first.goto == second.goto
