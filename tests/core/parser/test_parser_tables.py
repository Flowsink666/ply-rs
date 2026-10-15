"""Mutable parser tables, production bindings, and metadata refresh."""

from collections import defaultdict
from types import SimpleNamespace

import pytest

from ply_rs import Grammar, LRParser, LRTable, Token
from ply_rs.core.production import ParserError
from ply_rs.core.table_data import ParseTableData

from tests.fixtures.core import parser_for, table_for


@pytest.mark.parametrize("kind,replace_dictionary,terminal,start,value", [
    pytest.param("action", False, "NUMBER", "result", 42, id="action-entry"),
    pytest.param("goto", False, "NUMBER", "result", 42, id="goto-entry"),
    pytest.param("action", True, "N", "s", 1, id="action-dictionary"),
])
def test_builder_table_edits_apply_to_new_parser(kind, replace_dictionary, terminal, start, value):
    table = table_for([terminal], start=start)
    tokens = [Token(terminal, value)]
    # Creating multiple parsers must leave the shared table unchanged.
    for _ in range(2):
        assert LRParser(table).parse(tokens) == value
    rows = getattr(table, kind)
    if replace_dictionary:
        rows = {state: row.copy() for state, row in rows.items()}
        setattr(table, kind, rows)
    rows[0][terminal if kind == "action" else start] = None if kind == "action" else 0
    with pytest.raises(SyntaxError):
        LRParser(table).parse(tokens)


def test_values_action_can_be_rebound_between_parses():
    grammar = Grammar({"NUMBER"})
    grammar.add_production("result", ["NUMBER"], func="number", action_style="values")
    grammar.set_start("result")
    production = grammar.Productions[1]
    production.bind({"number": lambda value: value + 1})
    parser = LRParser(LRTable(grammar))
    assert parser.parse([Token("NUMBER", 42)]) == 43
    production.func = lambda value: value + 2
    assert parser.parse([Token("NUMBER", 42)]) == 44


def test_error_callback_updates_apply_to_the_next_parse():
    grammar = Grammar({"N", "BAD"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar), error_func=lambda tok: "first")
    assert parser.parse([Token("BAD", "?")]) == "first"
    parser.error_func = lambda tok: "second"
    assert parser.parse([Token("BAD", "?")]) == "second"


def test_parser_accepts_table_data_without_builder_diagnostics():
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    table = SimpleNamespace(
        productions=grammar.Productions,
        action={0: {"N": 1}, 1: {"$end": -1}, 2: {"$end": 0}},
        goto={0: {"s": 2}},
    )
    assert LRParser(table).parse([Token("N", 42)]) == 42


def single_token_data():
    return ParseTableData.from_table(table_for(["N"], lambda p: p[1]))


def test_missing_action_row_does_not_invoke_mapping_defaults():
    data = single_token_data()
    first_row = data.action[0]
    data.action = defaultdict(lambda: first_row, data.action)
    del data.action[0]
    with pytest.raises(SyntaxError):
        LRParser(data).parse([Token("N", 1)])
    assert 0 not in data.action


@pytest.mark.parametrize("missing", ["outer", "inner", "none"])
def test_missing_goto_still_raises_without_inventing_mapping_entries(missing):
    data = single_token_data()
    original_row = data.goto[0]
    if missing == "outer":
        data.goto = defaultdict(lambda: original_row, data.goto)
        del data.goto[0]
    elif missing == "inner":
        row = data.goto[0] = defaultdict(lambda: original_row["s"])
    else:
        data.goto[0] = None
    with pytest.raises(ParserError, match="No GOTO entry"):
        LRParser(data).parse([Token("N", 1)])
    if missing == "outer":
        assert 0 not in data.goto
    elif missing == "inner":
        assert "s" not in row


def make_parser(function=None, style="proxy"):
    return parser_for(["N"], function, style)


@pytest.mark.parametrize("initial,changed", [
    ("none", "proxy"), ("none", "values"), ("proxy", "proxy2"),
    ("values", "values2"), ("proxy", "none"), ("values", "none"),
    ("proxy", "values"), ("values", "proxy"),
])
def test_action_presence_style_and_function_updates_between_parses(initial, changed):
    actions = {
        "none": (None, "proxy", 1),
        "proxy": (lambda p: p[1] + 10, "proxy", 11),
        "proxy2": (lambda p: p[1] + 20, "proxy", 21),
        "values": (lambda value: value + 10, "values", 11),
        "values2": (lambda value: value + 20, "values", 21),
    }
    function, style, expected = actions[initial]
    parser = make_parser(function, style)
    assert parser.parse([Token("N", 1)]) == expected
    function, style, expected = actions[changed]
    parser.productions[1].func = function
    parser.productions[1].action_style = style
    assert parser.parse([Token("N", 1)]) == expected


@pytest.mark.parametrize("replace_dictionary", [False, True])
@pytest.mark.parametrize("kind", ["action", "goto"])
def test_public_table_edits_apply_to_existing_parser(kind, replace_dictionary):
    parser = make_parser()
    assert parser.parse([Token("N", 1)]) == 1
    rows = getattr(parser, kind)
    if replace_dictionary:
        rows = {state: row.copy() for state, row in rows.items()}
        setattr(parser, kind, rows)
    if kind == "action":
        previous = rows[0]["N"]
        rows[0]["N"] = None
        expected_error, message = SyntaxError, None
    else:
        previous = rows[0].pop("s")
        expected_error, message = (ParserError, RuntimeError), "No GOTO"
    with pytest.raises(expected_error, match=message):
        parser.parse([Token("N", 1)])
    rows[0]["N" if kind == "action" else "s"] = previous
    assert parser.parse([Token("N", 2)]) == 2


def test_replaced_productions_apply_to_existing_parser():
    parser = make_parser()
    replacement = make_parser(lambda value: value + 10, "values")
    parser.productions = replacement.productions
    assert parser.parse([Token("N", 1)]) == 11


def test_recursive_parse_can_refresh_metadata_without_changing_outer_engine():
    def outer(p):
        parser.productions[1].func = lambda value: value + 10
        parser.productions[1].action_style = "values"
        inner = parser.parse([Token("N", 2)])
        return p.parser.name, p[1], inner

    parser = make_parser(outer)
    parser.name = "outer"
    assert parser.parse([Token("N", 1)]) == ("outer", 1, 12)
    assert parser.parse([Token("N", 3)]) == 13


def test_native_metadata_refresh_reuses_an_edited_runtime_table(monkeypatch):
    parser = make_parser()
    # Keep a valid edit that prevents reuse of the builder's original artifact.
    parser.action[0]["UNUSED"] = None
    assert parser.parse([Token("N", 1)]) == 1
    native_class = parser._runtime.module.LRParserEngine

    def unexpected(*args, **kwargs):
        raise AssertionError("metadata-only edit reconstructed native table rows")

    monkeypatch.setattr(parser._runtime.module, "LRParserEngine", SimpleNamespace(
        from_engine=native_class.from_engine, from_table=unexpected,
    ))
    parser.productions[1].func = lambda p: p[1] + 10
    assert parser.parse([Token("N", 1)]) == 11


@pytest.mark.parametrize("action", [-999, -(1 << 31), -1])
def test_invalid_reductions_raise_parser_error_without_native_panic(action):
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    parser.action[0]["N"] = action
    with pytest.raises(ParserError, match="Invalid reduction"):
        parser.parse([Token("N", 7)])
    assert parser._sessions == []


@pytest.mark.parametrize("field", ["action", "goto"])
def test_invalid_transition_target_raises_parser_error(field):
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    if field == "action":
        parser.action[0]["N"] = 999
    else:
        parser.goto[0]["s"] = 999
    with pytest.raises(ParserError, match="Invalid .* state"):
        parser.parse([Token("N", 7)])
    assert parser._sessions == []
