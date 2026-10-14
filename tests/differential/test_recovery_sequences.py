"""Recovery contracts against independent specifications and vendored PLY.

The exhaustive comparison shares ACTION/GOTO to isolate execution. The smaller
comparisons let PLY build its own tables so table compilation is checked too.
References are loaded by upstream_ply under private names, never via sys.path.
"""

from itertools import product
from types import SimpleNamespace

import pytest

from rply import Grammar, LRParser, LRTable, Token


pytestmark = pytest.mark.differential


STATEMENT_RULES = (
    ("s", ("START", "stmts"), lambda p: p[2]),
    ("stmts", ("stmts", "stmt"), lambda p: p[1] + [p[2]]),
    ("stmts", ("stmt",), lambda p: [p[1]]),
    ("stmt", ("ID", "SEMI"), lambda p: ("id", p[1])),
    ("stmt", ("error", "SEMI"), lambda p: ("error", p[1])),
)


def _grammar(terminals, rules, start, precedence=()):
    grammar = Grammar(set(terminals))
    grammar.set_precedence(list(precedence))
    for name, rhs, action in rules:
        grammar.add_production(name, list(rhs), action)
    grammar.set_start(start)
    return grammar


def _ply_action(action):
    def reduce(proxy):
        # PLY exposes the original token as error.value; rply exposes its value.
        for symbol in proxy.slice[1:]:
            if symbol.type == "error" and hasattr(symbol.value, "value"):
                symbol.value = symbol.value.value
        proxy[0] = action(proxy)

    return reduce


def _ply_parser(yacc, table, errors):
    parser = yacc.LRParser(
        table, lambda token: errors.append(token.value if token is not None else "$end")
    )
    # Default reductions can change when errors are detected. This comparison
    # checks the explicit lookahead contract common to both runtimes.
    parser.disable_defaulted_states()
    return parser


def _shared_table_oracle(yacc, table, errors):
    productions = [
        SimpleNamespace(
            name=production.name,
            len=production.len,
            callable=_ply_action(production.func) if production.func is not None else None,
        )
        for production in table.productions
    ]
    return _ply_parser(yacc, SimpleNamespace(
        lr_action=table.action, lr_goto=table.goto, lr_productions=productions
    ), errors)


def _independent_oracle(yacc, terminals, rules, start, errors, precedence=()):
    grammar = yacc.Grammar(list(terminals))
    for token, (associativity, level) in precedence:
        grammar.set_precedence(token, associativity, level)
    actions = {}
    for index, (name, rhs, action) in enumerate(rules):
        action_name = f"action_{index}"
        actions[action_name] = _ply_action(action)
        grammar.add_production(name, list(rhs), func=action_name)
    grammar.set_start(start)
    table = yacc.LRTable(grammar)
    table.bind_callables(actions)
    return _ply_parser(yacc, table, errors)


def _run_rply(parser, entries):
    try:
        result = parser.parse([Token(kind, value) for kind, value in entries])
    except SyntaxError:
        return False, None
    return True, result


def _run_ply(parser, entries):
    source = iter(SimpleNamespace(type=kind, value=value, lineno=1, lexpos=index)
                  for index, (kind, value) in enumerate(entries))
    result = parser.parse(lexer=SimpleNamespace(token=lambda: next(source, None)))
    return result is not None, result


def _statement_spec(types, *, synthetic_shift_counts):
    """Three-state specification of semicolon-terminated statements, without LR.

    Only ID SEMI is valid. An invalid statement retains its first unexpected
    token's value, discards input through the next SEMI, then starts a new one.
    rply requires three real shifts before its next error callback. PLY counts
    the synthetic shift too, and resets suppression while discarding errors.
    """
    assert types[0] == "START"
    state, remaining = "start", 0
    statements, callbacks = [], []
    statement_value = error_value = None

    for index, kind in enumerate(types[1:], 1):
        if state == "start" and kind == "ID":
            state, statement_value = "id", index
            remaining = max(remaining - 1, 0)
        elif state == "id" and kind == "SEMI":
            statements.append(("id", statement_value))
            state = "start"
            remaining = max(remaining - 1, 0)
        elif state == "error" and kind == "SEMI":
            statements.append(("error", error_value))
            state = "start"
            remaining = max(remaining - 1, 0)
        else:
            if remaining == 0:
                callbacks.append(index)
            if state != "error":
                state, error_value = "error", index
                remaining = 3 - int(synthetic_shift_counts)
                if kind == "SEMI":
                    statements.append(("error", error_value))
                    state = "start"
                    remaining -= 1
                    continue
            # PLY resets after replaying or discarding an offending token.
            if synthetic_shift_counts:
                remaining = 3

    successful = state == "start" and bool(statements)
    if not successful and remaining == 0:
        callbacks.append("$end")
    return (successful, statements if successful else None), callbacks


@pytest.mark.parametrize("frozen", [False, True], ids=["dynamic", "frozen"])
def test_all_short_recovery_sequences_have_exact_results_and_callbacks(upstream_ply, frozen):
    table = LRTable(_grammar({"START", "ID", "SEMI", "BAD"}, STATEMENT_RULES, "s"))
    errors, reference_errors = [], []
    parser = LRParser(table, error_func=lambda token: errors.append(token.value), frozen=frozen)
    reference = _shared_table_oracle(upstream_ply[1], table, reference_errors)
    successful = failed = successful_callback_differences = total = 0

    # Increasing lengths ensure the first failure is a shortest counterexample.
    for size in range(8):
        for suffix in product(("ID", "BAD", "SEMI"), repeat=size):
            types = ("START",) + suffix
            entries = tuple((kind, index) for index, kind in enumerate(types))
            errors.clear()
            reference_errors.clear()
            expected, expected_errors = _statement_spec(types, synthetic_shift_counts=False)
            reference_expected, expected_reference_errors = _statement_spec(
                types, synthetic_shift_counts=True
            )
            actual = _run_rply(parser, entries)
            reference_actual = _run_ply(reference, entries)
            trace = " ".join(types) + " $end"
            assert actual == expected == reference_actual == reference_expected, trace
            assert errors == expected_errors, (trace, "rply callbacks", errors, expected_errors)
            assert reference_errors == expected_reference_errors, (
                trace, "PLY callbacks", reference_errors, expected_reference_errors
            )
            total += 1
            if actual[0]:
                successful += 1
                successful_callback_differences += errors != reference_errors
            else:
                failed += 1

    assert (total, successful, failed) == (3280, 1093, 2187)
    # Every discrepancy is already checked against both explicit contracts;
    # this aggregate also locks the observed boundary of their difference.
    assert successful_callback_differences == 148


@pytest.mark.parametrize("frozen", [False, True], ids=["dynamic", "frozen"])
def test_independently_built_tables_preserve_recovery(upstream_ply, frozen):
    terminals = {"START", "ID", "SEMI", "BAD"}
    errors, reference_errors = [], []
    parser = LRParser(LRTable(_grammar(terminals, STATEMENT_RULES, "s")),
                      error_func=lambda token: errors.append(token.value), frozen=frozen)
    reference = _independent_oracle(upstream_ply[1], terminals, STATEMENT_RULES, "s", reference_errors)
    cases = (
        ("START", "ID", "SEMI"),
        ("START", "BAD", "SEMI", "BAD", "SEMI"),
        ("START", "BAD", "SEMI", "ID", "SEMI", "BAD", "SEMI"),
        ("START", "SEMI", "ID", "ID", "SEMI"),
        ("START", "BAD", "SEMI", "ID"),
    )
    for types in cases:
        entries = tuple((kind, index) for index, kind in enumerate(types))
        errors.clear()
        reference_errors.clear()
        expected, expected_errors = _statement_spec(types, synthetic_shift_counts=False)
        reference_expected, expected_reference_errors = _statement_spec(
            types, synthetic_shift_counts=True
        )
        assert _run_rply(parser, entries) == expected == _run_ply(reference, entries) == reference_expected, types
        assert errors == expected_errors, types
        assert reference_errors == expected_reference_errors, types


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
    # final numeric mapping rather than deriving it from rply's table/adapter.
    reference = _independent_oracle(upstream_ply[1], {"N", "A", "B", "C"}, rules,
                                    "e", [], expected_precedence.items())
    entries = (("N", 1), ("C", "c"), ("N", 2), ("C", "c"), ("N", 3))
    expected = True, (1, (2, 3))
    assert _run_rply(parser, entries) == expected == _run_ply(reference, entries)


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
    assert _run_rply(parser, entries) == expected == _run_ply(reference, entries)
    assert errors == reference_errors == ["<"]
