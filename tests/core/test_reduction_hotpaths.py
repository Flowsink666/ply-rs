"""Reduction optimizations preserve arguments, spans, callbacks, and saved slices."""

import pytest
import importlib
from types import SimpleNamespace

from rply import Grammar, LRParser, LRTable, Token
from rply.core.parser_context import ParseContext
from rply.core.table_data import ParseTableData


@pytest.mark.parametrize("arity", range(6))
@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("returns_none", [False, True])
def test_value_actions_preserve_arity_identity_defaults_and_spans(arity, frozen, returns_none):
    values = [object() for _ in range(arity)]
    calls, saved = [], []

    def action(*args):
        calls.append(args)
        return None if returns_none else args

    def root(proxy):
        saved.append(proxy)
        return proxy[1]

    grammar = Grammar({"N"})
    grammar.add_production("root", ["items"], root)
    grammar.add_production("items", ["N"] * arity, action, action_style="values")
    grammar.set_start("root")
    parser = LRParser(LRTable(grammar), frozen=frozen)
    tokens = [Token("N", value, lineno=i + 2, lexpos=i * 3, end=i * 3 + 1)
              for i, value in enumerate(values)]
    result = parser.parse(tokens)
    if returns_none:
        assert result is (values[0] if arity else None)
    else:
        assert result == tuple(values)
    assert len(calls) == 1
    assert all(actual is expected for actual, expected in zip(calls[0], values))
    assert len(calls[0]) == arity
    assert saved[0].linespan(1) == ((2, arity + 1) if arity else (1, 1))
    assert saved[0].lexspan(1) == ((0, (arity - 1) * 3 + 1) if arity else (0, 0))


@pytest.mark.parametrize("arity", range(6))
@pytest.mark.parametrize("failure_type", [RuntimeError, StopIteration])
def test_value_action_exceptions_propagate_once_before_parent_reduction(arity, failure_type):
    calls = []
    failure = failure_type("value action failed")

    def action(*args):
        calls.append(args)
        raise failure

    grammar = Grammar({"N"})
    grammar.add_production("root", ["items"], lambda p: calls.append("parent"))
    grammar.add_production("items", ["N"] * arity, action, action_style="values")
    grammar.set_start("root")
    parser = LRParser(LRTable(grammar))
    with pytest.raises(failure_type) as caught:
        parser.parse([Token("N", i) for i in range(arity)])
    assert caught.value is failure
    assert calls == [tuple(range(arity))]


@pytest.mark.parametrize("frozen", [False, True])
def test_empty_proxy_reductions_keep_distinct_saved_slices_and_live_stack(frozen):
    saved = []

    def empty(proxy):
        saved.append(proxy)
        proxy[0] = proxy[-1]
        proxy.set_lineno(0, 7)
        proxy.set_lexpos(0, 8)

    grammar = Grammar({"N"})
    grammar.add_production("root", ["N", "empty", "empty"], lambda p: (p[2], p[3]))
    grammar.add_production("empty", [], empty)
    grammar.set_start("root")
    parser = LRParser(LRTable(grammar), frozen=frozen)
    assert parser.parse([Token("N", 4)]) == (4, 4)
    assert len(saved) == 2
    assert saved[0] is not saved[1]
    assert saved[0].slice is not saved[1].slice
    assert [list(p[:]) for p in saved] == [[4], [4]]
    assert [(p.lineno(0), p.lexpos(0)) for p in saved] == [(7, 8), (7, 8)]
    saved[0][0] = 99
    assert saved[1][0] == 4


@pytest.mark.parametrize("instance_hook", [False, True])
def test_custom_reduction_context_keeps_rhs_only_arguments(instance_hook):
    calls = []
    original = ParseContext.reduce

    def hook(production, lhs, popped, stack):
        calls.append([(s.type, s.value) for s in popped])
        return original(context, production, lhs, popped, stack)

    class Context(ParseContext):
        def reduce(self, production, lhs, popped, stack):
            return hook(production, lhs, popped, stack)

    context = ParseContext() if instance_hook else Context()
    if instance_hook:
        object.__setattr__(context, "reduce", hook)
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], lambda p: p[1])
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    assert parser._runtime.parse([Token("N", 7)], context=context) == 7
    assert calls == [[("N", 7)]]


def test_older_native_interface_keeps_its_original_parse_keywords():
    from rply.backends.rust_parser import RustParserRuntime

    native = importlib.import_module("rply._core_rs")
    calls = []

    class OlderEngine:
        # This interface deliberately has no prepared-slice capability marker.
        from_engine = native.LRParserEngine.from_engine

        def __init__(self, *args):
            self.engine = native.LRParserEngine(*args)

        def parse(self, tokens, reduce_cb, error_cb, value_funcs, *, with_stack=False):
            calls.append(with_stack)
            return self.engine.parse(tokens, reduce_cb, error_cb, value_funcs, with_stack=with_stack)

    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], lambda p: p[1])
    grammar.set_start("s")
    data = ParseTableData.from_table(LRTable(grammar))
    runtime = RustParserRuntime(data, SimpleNamespace(LRParserEngine=OlderEngine))
    assert runtime.parse([Token("N", 7)], context=ParseContext()) == 7
    assert calls == [True]


@pytest.mark.parametrize("with_stack", [False, True])
@pytest.mark.parametrize("arity", [0, 1])
def test_low_level_native_callback_keeps_default_rhs_only_protocol(with_stack, arity):
    native = importlib.import_module("rply._core_rs")
    if arity:
        action = {0: {"N": 1}, 1: {"$end": -1}, 2: {"$end": 0}}
        goto = {0: {"s": 2}}
    else:
        action = {0: {"$end": -1}, 1: {"$end": 0}}
        goto = {0: {"s": 1}}
    engine = native.LRParserEngine(len(action), action, goto, ["S'", "s"], [1, arity], [False, True])
    calls = []

    def reduce_cb(number, lhs, popped, *stack):
        calls.append((number, lhs.type, [s.type for s in popped], len(stack)))
        return lhs.value

    result = engine.parse([Token("N", 7)] if arity else [], reduce_cb, with_stack=with_stack)
    assert result == (7 if arity else None)
    assert calls == [(1, "s", ["N"] if arity else [], int(with_stack))]


def test_replaced_default_value_finalizer_observes_committed_lhs():
    trace, saved = [], []

    class Value:
        def __del__(self):
            try:
                trace.append(("released", saved[0][0]))
            except Exception as error:
                trace.append(("release-error", type(error).__name__))

    def action(proxy):
        saved.append(proxy)
        proxy[1] = None
        proxy[0] = 0
        trace.append("action-end")
        return 0

    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], action)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))

    def tokens():
        yield Token("N", Value())

    assert parser.parse(tokens()) == 0
    expected = ["action-end", ("released", 0)] if parser.backend == "rust" else [
        ("released", 0), "action-end",
    ]
    assert trace == expected


def test_slice_deletion_preserves_rhs_release_order_and_timing():
    trace, saved = [], []

    class Value:
        def __init__(self, number):
            self.number = number

        def __del__(self):
            trace.append(("released", self.number, saved[0][0] == 0))

    def action(proxy):
        saved.append(proxy)
        del proxy.slice[1:]
        proxy[0] = 0
        trace.append("action-end")
        return 0

    grammar = Grammar({"N"})
    grammar.add_production("s", ["N", "N", "N"], action)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))

    def tokens():
        for number in (1, 2, 3):
            yield Token("N", Value(number))

    assert parser.parse(tokens()) == 0
    expected = (["action-end", ("released", 3, True), ("released", 2, True), ("released", 1, True)]
                if parser.backend == "rust" else [
                    ("released", 3, False), ("released", 2, False), "action-end", ("released", 1, True),
                ])
    assert trace == expected


def test_failed_slice_action_retains_rhs_in_its_traceback():
    trace = []
    failure = RuntimeError("slice action failed")

    class Value:
        def __init__(self, number):
            self.number = number

        def __del__(self):
            trace.append(self.number)

    def action(proxy):
        del proxy.slice[1:]
        proxy[0] = 0
        trace.append("action-end")
        raise failure

    grammar = Grammar({"N"})
    grammar.add_production("s", ["N", "N", "N"], action)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))

    def tokens():
        for number in (1, 2, 3):
            yield Token("N", Value(number))

    try:
        parser.parse(tokens())
    except RuntimeError as error:
        assert error is failure
    else:
        pytest.fail("slice action exception was lost")
    expected = ["action-end"] if parser.backend == "rust" else [3, 2, "action-end"]
    assert trace == expected
    failure.__traceback__ = None
    assert trace == (["action-end", 3, 2, 1] if parser.backend == "rust" else expected + [1])
