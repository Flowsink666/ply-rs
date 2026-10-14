"""A frozen parser snapshots bindings while preserving live callback context."""

from types import SimpleNamespace

import pytest

from rply import Grammar, LRParser, LRTable, Token
from rply.modern.parser import Parser, rule


def make_table(function=None, style="proxy"):
    grammar = Grammar({"N", "BAD"})
    grammar.add_production("s", ["N"], function, action_style=style)
    grammar.set_start("s")
    return LRTable(grammar)


@pytest.mark.parametrize("at_construction", [False, True])
@pytest.mark.parametrize("style", ["proxy", "values"])
def test_frozen_parser_detaches_tables_and_production_bindings(at_construction, style):
    function = (lambda p: p[1] + 1) if style == "proxy" else (lambda value: value + 1)
    table = make_table(function, style)
    parser = LRParser(table, frozen=at_construction)
    if not at_construction:
        assert parser.freeze() is parser
    assert parser.frozen
    assert parser.parse([Token("N", 4)]) == 5
    table.productions[1].func = lambda value: 999
    table.productions[1].action_style = "values"
    table.productions[1].name = "changed"
    table.productions[1].namemap.clear()
    table.action[0]["N"] = None
    table.goto.clear()
    assert parser.parse([Token("N", 4)]) == 5
    with pytest.raises(SyntaxError):
        LRParser(table).parse([Token("N", 4)])


def test_freeze_observes_pending_edits_and_is_idempotent():
    table = make_table(lambda p: p[1])
    parser = LRParser(table)
    assert parser.parse([Token("N", 2)]) == 2
    table.productions[1].func = lambda value: value + 10
    table.productions[1].action_style = "values"
    table.action[0]["UNUSED"] = None
    parser.freeze()
    runtime = parser._runtime
    assert parser.parse([Token("N", 2)]) == 12
    assert parser.freeze() is parser
    assert parser._runtime is runtime
    table.productions[1].func = lambda value: value + 100
    assert parser.parse([Token("N", 2)]) == 12


def test_frozen_views_reject_nested_edits_and_replacement():
    parser = LRParser(make_table(), frozen=True)
    for name in ("action", "goto", "productions"):
        with pytest.raises(TypeError, match="Frozen"):
            setattr(parser, name, getattr(parser, name))
    with pytest.raises(TypeError):
        parser.action[0]["N"] = None
    with pytest.raises(TypeError):
        parser.goto[0]["s"] = 0
    with pytest.raises(TypeError, match="Frozen"):
        parser.productions[1].func = lambda p: 999
    with pytest.raises(TypeError, match="Frozen"):
        del parser.productions[1].func
    with pytest.raises(TypeError, match="Frozen"):
        del parser.productions[1]._sealed
    with pytest.raises(TypeError):
        parser.productions[1].namemap["N"] = 0
    assert parser.parse([Token("N", 4)]) == 4


def test_frozen_parser_keeps_error_callback_updates_live():
    parser = LRParser(make_table(), lambda tok: "first", frozen=True)
    assert parser.parse([Token("BAD", "?")]) == "first"
    parser.error_func = lambda tok: "second"
    assert parser.parse([Token("BAD", "?")]) == "second"


@pytest.mark.parametrize("at_construction", [False, True])
def test_freezing_retains_automatic_name_map_generation(at_construction):
    table = make_table(lambda p: p.N)
    table.productions[1].namemap = None
    parser = LRParser(table, frozen=at_construction)
    if not at_construction:
        assert parser.parse([Token("N", 42)]) == 42
        parser.freeze()
    assert parser.productions[1].namemap is None
    assert parser.parse([Token("N", 42)]) == 42


def test_freeze_failure_leaves_the_existing_runtime_editable(monkeypatch):
    parser = LRParser(make_table())
    runtime, data = parser._runtime, parser._data

    def failed(data):
        raise ValueError("failed to compile snapshot")

    monkeypatch.setattr(parser, "_create_runtime", failed)
    with pytest.raises(ValueError, match="failed to compile"):
        parser.freeze()
    assert not parser.frozen
    assert parser._runtime is runtime and parser._data is data
    parser.productions[1].func = lambda p: p[1] + 10
    assert parser.parse([Token("N", 2)]) == 12


@pytest.mark.parametrize("frozen", [False, True])
def test_modern_actions_receive_public_owner_and_independent_recursive_context(frozen):
    class OwnerParser(Parser):
        tokens = {"N"}

        @rule("s : N")
        def value(self, p):
            assert p.parser is self
            if p[1] == 2:
                inner = self.parse([Token("N", 1)], lexer=SimpleNamespace(name="inner"))
                return p.lexer.name, inner, self.increment
            return p.lexer.name, self.increment

    parser = OwnerParser()
    parser.increment = 10
    if frozen:
        assert parser.freeze() is parser
    assert parser.frozen is frozen
    assert parser.parse([Token("N", 2)], lexer=SimpleNamespace(name="outer")) == (
        "outer", ("inner", 10), 10,
    )
    parser.increment = 20
    assert parser.parse([Token("N", 1)], lexer=SimpleNamespace(name="changed")) == ("changed", 20)
