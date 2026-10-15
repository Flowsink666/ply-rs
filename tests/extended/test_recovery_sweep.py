"""Exhaustive recovery execution comparisons, run explicitly."""

from itertools import product

import pytest

from ply_rs import LRParser, LRTable
from tests.fixtures.recovery import (STATEMENT_RULES, _grammar, _shared_table_oracle, _run_ply, _run_ply_rs)


def _statement_spec(types, *, synthetic_shift_counts):
    """Three-state specification of semicolon-terminated statements, without LR.

    Only ID SEMI is valid. An invalid statement retains its first unexpected
    token's value, discards input through the next SEMI, then starts a new one.
    ply-rs requires three real shifts before its next error callback. PLY counts
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
            actual = _run_ply_rs(parser, entries)
            reference_actual = _run_ply(reference, entries)
            trace = " ".join(types) + " $end"
            assert actual == expected == reference_actual == reference_expected, trace
            assert errors == expected_errors, (trace, "ply_rs callbacks", errors, expected_errors)
            assert reference_errors == expected_reference_errors, (
                trace, "PLY callbacks", reference_errors, expected_reference_errors
            )
