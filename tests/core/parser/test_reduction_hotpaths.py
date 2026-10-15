"""Value-action arguments, reduction hooks, and symbol lifetimes."""

from functools import wraps
from types import SimpleNamespace
import importlib

import pytest

from ply_rs import Grammar, LRParser, LRTable, Token
from ply_rs.core.parser_context import ParseContext
from ply_rs.core.table_data import ParseTableData

from tests.fixtures.core import parser_for, table_for


@pytest.mark.parametrize("arity,frozen,returns_none", [
    # Native dispatch has dedicated paths for 0..3 and a tuple path for >=4.
    *[pytest.param(arity, False, False, id=f"arity-{arity}") for arity in range(5)],
    pytest.param(0, False, True, id="empty-default"),
    pytest.param(1, False, True, id="first-value-default"),
    pytest.param(4, True, False, id="frozen-tuple"),
    pytest.param(3, True, True, id="frozen-default"),
])
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


@pytest.mark.parametrize("action_style,arity,failure_type", [
    pytest.param("values", arity, failure_type,
                 id=f"values-{arity}-{failure_type.__name__}")
    for arity in range(5)
    for failure_type in (RuntimeError,)
] + [
    pytest.param("values", 1, StopIteration, id="values-StopIteration"),
    pytest.param("auto", 1, TypeError, id="proxy-TypeError"),
    pytest.param("auto", 1, ValueError, id="proxy-ValueError"),
    pytest.param("values", 1, TypeError, id="values-1-TypeError"),
])
def test_action_exceptions_propagate_once_before_parent_reduction(
    action_style, arity, failure_type,
):
    calls = []
    failure = failure_type("action failed")

    def fail(*args):
        calls.append(args)
        raise failure

    if action_style == "auto":
        def action(proxy):
            return fail(*(proxy[index] for index in range(1, arity + 1)))
    else:
        action = fail

    grammar = Grammar({"N"})
    grammar.add_production("root", ["items"], lambda p: calls.append("parent"))
    grammar.add_production("items", ["N"] * arity, action, action_style=action_style)
    grammar.set_start("root")
    parser = LRParser(LRTable(grammar))
    with pytest.raises(failure_type, match="action failed") as caught:
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
    parser = parser_for(["N"], lambda p: p[1])
    assert parser._runtime.parse([Token("N", 7)], context=context) == 7
    assert calls == [[("N", 7)]]


def test_older_native_interface_keeps_its_original_parse_keywords():
    from ply_rs.backends.rust_parser import RustParserRuntime

    native = importlib.import_module("ply_rs._core_rs")
    calls = []

    class OlderEngine:
        # This interface deliberately has no prepared-slice capability marker.
        from_engine = native.LRParserEngine.from_engine

        def __init__(self, *args):
            self.engine = native.LRParserEngine(*args)

        def parse(self, tokens, reduce_cb, error_cb, value_funcs, *, with_stack=False):
            calls.append(with_stack)
            return self.engine.parse(tokens, reduce_cb, error_cb, value_funcs, with_stack=with_stack)

    data = ParseTableData.from_table(table_for(["N"], lambda p: p[1]))
    runtime = RustParserRuntime(data, SimpleNamespace(LRParserEngine=OlderEngine))
    assert runtime.parse([Token("N", 7)], context=ParseContext()) == 7
    assert calls == [True]


@pytest.mark.parametrize("with_stack", [False, True])
@pytest.mark.parametrize("arity", [0, 1])
def test_low_level_native_callback_keeps_default_rhs_only_protocol(with_stack, arity):
    native = importlib.import_module("ply_rs._core_rs")
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

    parser = parser_for(["N"], action)

    def tokens():
        yield Token("N", Value())

    assert parser.parse(tokens()) == 0
    assert trace == ["action-end", ("released", 0)]


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

    parser = parser_for(["N"] * 3, action)

    def tokens():
        for number in (1, 2, 3):
            yield Token("N", Value(number))

    assert parser.parse(tokens()) == 0
    assert trace == [
        "action-end", ("released", 3, True), ("released", 2, True), ("released", 1, True),
    ]


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

    parser = parser_for(["N"] * 3, action)

    def tokens():
        for number in (1, 2, 3):
            yield Token("N", Value(number))

    try:
        parser.parse(tokens())
    except RuntimeError as error:
        assert error is failure
    else:
        pytest.fail("slice action exception was lost")
    assert trace == ["action-end"]
    failure.__traceback__ = None
    assert trace == ["action-end", 3, 2, 1]


@pytest.mark.parametrize("exception_type", [None, RuntimeError, StopIteration])
@pytest.mark.parametrize("decorated", [False, True])
def test_shared_context_reduction_hook_keeps_its_original_call_protocol(
    monkeypatch, exception_type, decorated,
):
    calls, actions = [], []
    original = ParseContext.reduce
    failure = exception_type("reduction hook failed") if exception_type is not None else None

    def hooked_reduce(self, production, lhs, popped, stack):
        calls.append([symbol.value for symbol in popped])
        if failure is not None:
            raise failure
        return original(self, production, lhs, popped, stack)

    if decorated:
        hooked_reduce = wraps(original)(hooked_reduce)
    monkeypatch.setattr(ParseContext, "reduce", hooked_reduce)
    grammar = Grammar({"N"})

    def action(p):
        actions.append(p[1])
        return p[1]

    grammar.add_production("s", ["N"], action)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    if failure is not None:
        with pytest.raises(exception_type) as caught:
            parser.parse([Token("N", 7)])
        assert caught.value is failure
        assert actions == []
    else:
        assert parser.parse([Token("N", 7)]) == 7
        assert actions == [7]
    assert calls == [[7]]
