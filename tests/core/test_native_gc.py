"""Tokens, reduction symbols, and saved proxies release Python reference cycles."""

import gc
import weakref

from rply import Grammar, LexerEngine, LRParser, LRTable, Token
from rply.backends import native_core
from rply.core.production import ParsedSymbol


class Holder:
    pass


def assert_collected(reference):
    gc.collect()
    assert reference() is None


def test_saved_lexer_token_does_not_keep_its_owner_alive():
    def cycle():
        lexer = LexerEngine()
        lexer.add_rule("INITIAL", "N", "n")
        lexer.saved = next(lexer.tokenize("n"))
        assert gc.is_tracked(lexer.saved)
        return weakref.ref(lexer)

    assert_collected(cycle())


def test_token_self_referential_value_is_collected():
    def cycle():
        lexer = LexerEngine()
        lexer.add_rule("INITIAL", "N", "n")
        token = next(lexer.tokenize("n"))
        holder = Holder()
        holder.token = token
        token.value = [token, holder]
        token.lexer = None
        return weakref.ref(holder)

    assert_collected(cycle())


def test_parsed_symbol_self_referential_value_is_collected():
    def cycle():
        symbol_class = native_core.ParsedSymbol if native_core is not None else ParsedSymbol
        symbol = symbol_class("N")
        assert gc.is_tracked(symbol)
        holder = Holder()
        holder.symbol = symbol
        symbol.value = [symbol, holder]
        return weakref.ref(holder)

    assert_collected(cycle())


def test_saved_reduction_proxy_releases_popped_symbol_values():
    references = []

    def action(proxy):
        value = proxy[1]
        value.saved = proxy
        references.append(weakref.ref(value))
        # The final parser stack holds a plain integer, so this cycle comes
        # from the symbols saved in the proxy's slice, not the live stack.
        return 0

    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], action)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    assert parser.parse([Token("N", Holder())]) == 0
    assert_collected(references[0])


def test_saved_empty_reduction_proxy_releases_live_stack_values():
    references = []

    def empty(proxy):
        value = proxy[-1]
        value.saved = proxy
        assert gc.is_tracked(proxy._stack)
        references.append(weakref.ref(value))

    grammar = Grammar({"N"})
    grammar.add_production("s", ["N", "tail"])
    grammar.add_production("tail", [], empty)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    parser.parse([Token("N", Holder())])
    assert_collected(references[0])


def test_cycle_finalizer_can_inspect_token_and_replace_lexer_input():
    calls = []

    class FinalizingValue:
        def __del__(self):
            try:
                token = self.token
                assert token.value is self
                token.lexer.input("replacement")
                calls.append(token.lexer.data)
            except BaseException as error:
                calls.append(type(error).__name__)

    def cycle():
        lexer = LexerEngine()
        lexer.add_rule("INITIAL", "N", "n")
        token = next(lexer.tokenize("n"))
        value = FinalizingValue()
        value.token = token
        token.value = value
        lexer.saved = token
        return weakref.ref(lexer), weakref.ref(value)

    lexer_ref, value_ref = cycle()
    assert_collected(lexer_ref)
    assert value_ref() is None
    assert calls == ["replacement"]
