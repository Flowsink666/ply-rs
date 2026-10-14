"""Native action views keep the public proxy and reference-lifetime contracts."""

from concurrent.futures import ThreadPoolExecutor
import gc
import weakref

import pytest

from rply import Grammar, LRParser, LRTable, ProductionProxy, Token
from rply.core import parser_context
from rply.core.production import ParsedSymbol


def make_parser(action, *, arity=2, frozen=False):
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"] * arity, action)
    grammar.set_start("s")
    return LRParser(LRTable(grammar), frozen=frozen)


@pytest.mark.parametrize("frozen", [False, True])
def test_value_edits_and_named_access_survive_slice_materialization(frozen):
    left, right, replacement = object(), object(), object()
    saved = []

    def action(proxy):
        assert type(proxy) is ProductionProxy
        assert len(proxy) == 3
        assert proxy[0] is proxy[1] is proxy.N0 is left
        assert proxy.N1 is right
        proxy[1] = replacement
        assert proxy.N0 is replacement
        symbols = proxy.slice
        assert isinstance(symbols, list)
        assert proxy.slice is symbols
        assert symbols[1].value is replacement
        symbols[2].value = left
        assert proxy.N1 is left
        proxy[0] = (proxy[1], proxy[2])
        proxy.set_lineno(0, 7)
        proxy.set_lexpos(0, 9)
        saved.append(proxy)

    parser = make_parser(action, frozen=frozen)
    assert parser.parse([Token("N", left, end=1), Token("N", right, end=1)]) == (replacement, left)
    assert saved[0].linespan(0) == (7, 1)
    assert saved[0].lexspan(0) == (9, 1)
    assert saved[0][:] == [(replacement, left), replacement, left]


def test_replacing_slice_lhs_does_not_replace_the_original_reduction_lhs():
    saved = []

    def action(proxy):
        saved.append(proxy)
        proxy.slice[0] = ParsedSymbol("replacement", 99)
        assert proxy[0] == 99

    parser = make_parser(action, arity=1)
    assert parser.parse([Token("N", 7)]) == 7
    assert saved[0][0] == 99


@pytest.mark.parametrize("hook", ["init", "new", "setattr", "factory"])
def test_custom_proxy_construction_keeps_the_materialized_protocol(monkeypatch, hook):
    calls = []
    original_init = ProductionProxy.__init__
    original_new = ProductionProxy.__new__
    original_setattr = ProductionProxy.__setattr__

    if hook == "init":
        def initialize(self, symbols, *args, **kwargs):
            assert isinstance(symbols, list)
            calls.append("init")
            original_init(self, symbols, *args, **kwargs)
        monkeypatch.setattr(ProductionProxy, "__init__", initialize)
    elif hook == "new":
        def create(cls, symbols, *args, **kwargs):
            assert isinstance(symbols, list)
            calls.append("new")
            return original_new(cls, symbols, *args, **kwargs)
        monkeypatch.setattr(ProductionProxy, "__new__", staticmethod(create))
    elif hook == "setattr":
        def assign(self, name, value):
            if name == "_slice":
                assert isinstance(value, list)
                calls.append("setattr")
            original_setattr(self, name, value)
        monkeypatch.setattr(ProductionProxy, "__setattr__", assign)
    else:
        class CustomProxy(ProductionProxy):
            def __init__(self, symbols, *args, **kwargs):
                assert isinstance(symbols, list)
                calls.append("factory")
                super().__init__(symbols, *args, **kwargs)
        monkeypatch.setattr(parser_context, "ProductionProxy", CustomProxy)

    assert make_parser(lambda proxy: proxy[1], arity=1).parse([Token("N", 7)]) == 7
    assert calls == [hook]


def test_rhs_finalizers_update_positions_before_lhs_snapshot():
    saved, trace = [], []

    class Value:
        def __init__(self, number):
            self.number = number

        def __del__(self):
            saved[0].set_lineno(0, 10 + self.number)
            trace.append(self.number)

    def action(proxy):
        saved.append(proxy)
        del proxy.slice[1:]
        proxy[0] = 0
        return 0

    grammar = Grammar({"N"})
    grammar.add_production("root", ["s"], lambda proxy: (proxy[1], proxy.lineno(1)))
    grammar.add_production("s", ["N"] * 3, action)
    grammar.set_start("root")
    parser = LRParser(LRTable(grammar))

    def tokens():
        for number in (1, 2, 3):
            yield Token("N", Value(number))

    # RHS values 3 and 2 are released before the native LHS snapshot. Value 1
    # remains protected by the established default-value guard until afterward.
    assert parser.parse(tokens()) == (0, 12)
    assert trace == [3, 2, 1]
    assert saved[0].lineno(0) == 11


def test_exception_frame_keeps_rhs_when_user_discards_proxy_argument():
    trace = []
    failure = RuntimeError("action failed")

    class Value:
        def __init__(self, number):
            self.number = number

        def __del__(self):
            trace.append(self.number)

    def action(proxy):
        del proxy.slice[1:]
        proxy[0] = 0
        del proxy
        raise failure

    def tokens():
        for number in (1, 2, 3):
            yield Token("N", Value(number))

    parser = make_parser(action, arity=3)
    with pytest.raises(RuntimeError) as caught:
        parser.parse(tokens())
    assert caught.value is failure
    assert trace == []
    del caught
    failure.__traceback__ = None
    assert trace == [3, 2, 1]


@pytest.mark.parametrize("materialized", [False, True])
def test_saved_proxy_can_be_inspected_from_another_thread(materialized):
    saved = []

    def action(proxy):
        saved.append(proxy)
        if materialized:
            assert len(proxy.slice) == 3
        return proxy[1] + proxy[2]

    parser = make_parser(action)
    assert parser.parse([Token("N", 3), Token("N", 4)]) == 7
    with ThreadPoolExecutor(max_workers=1) as worker:
        assert worker.submit(lambda: (len(saved[0]), saved[0][0], saved[0].N1)).result() == (3, 7, 4)


def test_custom_and_large_indices_keep_python_proxy_semantics():
    class Index:
        def __ge__(self, other):
            return True

        def __index__(self):
            return 1

    class IndexOnly:
        def __index__(self):
            return 1

    def action(proxy):
        assert proxy[Index()] == 7
        proxy[Index()] = 8
        assert proxy[True] == 8
        assert proxy[1] == 8
        with pytest.raises(TypeError):
            proxy[IndexOnly()]
        for index, error in ((10 ** 100, IndexError), (-(10 ** 100), OverflowError)):
            with pytest.raises(error):
                proxy[index]
            with pytest.raises(IndexError):
                proxy[index] = 0
        return proxy[1]

    assert make_parser(action, arity=1).parse([Token("N", 7)]) == 8


@pytest.mark.parametrize("materialized", [False, True])
def test_returned_proxy_cycles_release_native_and_python_symbols(materialized):
    class Holder:
        pass

    def cycle():
        holder = Holder()

        def action(proxy):
            if materialized:
                assert len(proxy.slice) == 2
            return proxy

        parser = make_parser(action, arity=1)
        holder.saved = parser.parse([Token("N", holder)])
        assert holder.saved[0] is holder.saved
        return weakref.ref(holder)

    reference = cycle()
    gc.collect()
    assert reference() is None
