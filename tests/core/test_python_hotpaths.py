"""Observable contracts around grouped scanning and LR hot-path changes."""

import re
import warnings
from collections import defaultdict
from functools import wraps

import pytest

from rply import Grammar, LexerEngine, LexError, LRParser, LRTable, Token
from rply.backends.python.lexer import PythonLexerEngine
from rply.backends.python.parser import PythonLRParser
from rply.core.parser_context import ParseContext
from rply.core.lexer_config import LexerConfig
from rply.core.production import ParserError
from rply.core.table_data import ParseTableData


@pytest.mark.parametrize("patterns,text,expected", [
    ((r"(a)\1", r"(b)\1"), "aa bb", [("A", "aa"), ("B", "bb")]),
    ((r"(?P<same>a)(?P=same)", r"(?P<same>b)(?P=same)"),
     "aa bb", [("A", "aa"), ("B", "bb")]),
    ((r"(a)?(?(1)b|c)", r"d"), "ab c d", [("A", "ab"), ("A", "c"), ("B", "d")]),
    ((r"(?i)alpha", r"beta"), "ALPHA beta", [("A", "ALPHA"), ("B", "beta")]),
    ((r"a", r"(?<=a)b"), "ab", [("A", "a"), ("B", "b")]),
    ((r"a", r"ab"), "a", [("A", "a")]),
])
def test_grouped_scanning_preserves_individual_regex_semantics(patterns, text, expected):
    # Backreferences and Python-only regex syntax need the Python engine even
    # when the surrounding suite is running with the Rust backend selected.
    lexer = PythonLexerEngine()
    lexer.set_ignore("INITIAL", " ")
    for name, pattern in zip(("A", "B"), patterns):
        lexer.add_rule("INITIAL", name, pattern)
    assert [(token.type, token.value) for token in lexer.tokenize(text)] == expected


def test_grouped_scanning_keeps_rule_flags_local():
    lexer = PythonLexerEngine()
    lexer.add_rule("INITIAL", "ASCII", r"\w+", flags=re.ASCII)
    lexer.add_rule("INITIAL", "UNICODE", r"\w+")
    lexer.set_ignore("INITIAL", " ")
    assert [(token.type, token.value) for token in lexer.tokenize("ascii 中文")] == [
        ("ASCII", "ascii"), ("UNICODE", "中文"),
    ]


def test_global_inline_flags_do_not_leak_into_neighbor_rules():
    lexer = PythonLexerEngine()
    lexer.add_rule("INITIAL", "A", r"(?i)alpha")
    lexer.add_rule("INITIAL", "B", r"beta")
    lexer.set_ignore("INITIAL", " ")
    with pytest.raises(LexError) as caught:
        list(lexer.tokenize("ALPHA BETA"))
    assert caught.value.error_index == 6


def test_global_inline_flags_keep_working_with_warnings_as_errors():
    # Python 3.9/3.10 warn, rather than reject, when a global flag is moved
    # inside a wrapper. Original valid patterns must not gain that warning.
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        lexer = PythonLexerEngine()
        lexer.add_rule("INITIAL", "A", r"(?i)alpha")
        lexer.add_rule("INITIAL", "B", r"beta", flags=re.IGNORECASE)
        lexer.set_ignore("INITIAL", " ")
        assert [(token.type, token.value) for token in lexer.tokenize("ALPHA BETA")] == [
            ("A", "ALPHA"), ("B", "BETA"),
        ]


@pytest.mark.parametrize("pattern,value", [
    (r"[[a]", "a"),
    (r"[a&&b]", "&"),
    (r"[a~~b]", "~"),
    (r"[a||b]", "|"),
    (r"[0-9--]", "-"),
])
def test_grouping_does_not_repeat_rule_validation_warnings(pattern, value):
    lexer = PythonLexerEngine()
    # The caller's policy at registration applies to the original expression.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        lexer.add_rule("INITIAL", "SET", pattern)
        lexer.add_rule("INITIAL", "X", "x")
    # Later filtering changes must not make a validated rule fail at scanning
    # merely because an optimization recompiles it inside a new capture group.
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        assert [(token.type, token.value) for token in lexer.tokenize(value + "x")] == [
            ("SET", value), ("X", "x"),
        ]


def test_verbose_trailing_comments_do_not_swallow_neighbor_rules():
    lexer = PythonLexerEngine()
    lexer.add_rule("INITIAL", "A", "a # trailing comment", flags=re.VERBOSE)
    lexer.add_rule("INITIAL", "B", "b", flags=re.VERBOSE)
    assert [(token.type, token.value) for token in lexer.tokenize("ab")] == [("A", "a"), ("B", "b")]


def test_fallback_rules_keep_their_order_between_grouped_rules():
    lexer = PythonLexerEngine()
    for name, pattern in [("X", "x"), ("Y", "y"), ("PAIR", r"(a)\1"),
                          ("A", "a+"), ("B", "b")]:
        lexer.add_rule("INITIAL", name, pattern)
    lexer.set_ignore("INITIAL", " ")
    assert [(token.type, token.value) for token in lexer.tokenize("x y aa a b")] == [
        ("X", "x"), ("Y", "y"), ("PAIR", "aa"), ("A", "a"), ("B", "b"),
    ]


def test_zero_length_first_match_still_raises_before_later_rules():
    lexer = LexerEngine()
    calls = []
    lexer.add_rule("INITIAL", "EMPTY", "a*", lambda token: calls.append(token))
    lexer.add_rule("INITIAL", "B", "b")
    with pytest.raises(LexError):
        list(lexer.tokenize("b"))
    assert calls == []


def test_ignored_callback_refreshes_grouped_rules_and_committed_input():
    lexer = LexerEngine()
    calls = []

    def replace_input(token):
        calls.append(token.value)
        token.lexer.add_rule("INITIAL", "NEW", "ab", priority=10)
        token.lexer.input("ab")

    lexer.add_rule("INITIAL", "REPLACE", "!", replace_input)
    lexer.add_rule("INITIAL", "A", "a")
    lexer.add_rule("INITIAL", "B", "b")
    tokens = list(lexer.tokenize("!old"))
    assert [(token.type, token.value) for token in tokens] == [("NEW", "ab")]
    assert calls == ["!"]
    assert lexer.data == "ab"
    assert lexer.pos == 2


def test_shifted_tokens_remain_snapshots_of_the_consumed_value():
    grammar = Grammar({"N"})
    grammar.add_production("pair", ["N", "N"], lambda p: (p[1], p[2]))
    grammar.set_start("pair")
    parser = LRParser(LRTable(grammar))
    first = Token("N", 1)

    def tokens():
        yield first
        first.value = 99
        yield Token("N", 2)

    assert parser.parse(tokens()) == (1, 2)


@pytest.mark.parametrize("frozen", [False, True])
def test_saved_proxies_are_distinct_within_one_parse(frozen):
    saved = []
    grammar = Grammar({"N"})

    def single(p):
        saved.append(p)
        p[0] = p[1]

    def append(p):
        saved.append(p)
        p[0] = p[1] + p[2]

    grammar.add_production("items", ["items", "N"], append)
    grammar.add_production("items", ["N"], single)
    grammar.set_start("items")
    parser = LRParser(LRTable(grammar), frozen=frozen)
    assert parser.parse([Token("N", value) for value in (1, 2, 3)]) == 6
    assert len({id(proxy) for proxy in saved}) == 3
    assert [list(proxy[:]) for proxy in saved] == [[1, 1], [3, 1, 2], [6, 3, 3]]


def single_token_data():
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], lambda p: p[1])
    grammar.set_start("s")
    return ParseTableData.from_table(LRTable(grammar))


def test_missing_action_row_does_not_invoke_mapping_defaults():
    data = single_token_data()
    first_row = data.action[0]
    data.action = defaultdict(lambda: first_row, data.action)
    del data.action[0]
    with pytest.raises(SyntaxError):
        PythonLRParser(data).parse([Token("N", 1)])
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
        PythonLRParser(data).parse([Token("N", 1)])
    if missing == "outer":
        assert 0 not in data.goto
    elif missing == "inner":
        assert "s" not in row


def test_custom_context_keeps_its_four_argument_reduction_protocol():
    calls = []

    class Context(ParseContext):
        def reduce(self, production, lhs, popped, stack):
            calls.append([symbol.value for symbol in popped])
            return super().reduce(production, lhs, popped, stack)

    assert PythonLRParser(single_token_data()).parse([Token("N", 7)], context=Context()) == 7
    assert calls == [[7]]


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


def test_invalid_lookahead_is_checked_before_a_single_possible_reduction():
    calls = []
    grammar = Grammar({"N", "SEMI", "BAD"})
    grammar.add_production("s", ["item", "SEMI"], lambda p: p[1])
    grammar.add_production("item", ["N"], lambda p: calls.append(p[1]))
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    with pytest.raises(SyntaxError):
        parser.parse([Token("N", 7), Token("BAD", "?")])
    assert calls == []


def test_warm_scanner_observes_shared_config_updates_in_every_runtime():
    config = LexerConfig()
    config.add_rule("INITIAL", "WORD", "[a-z]+")
    config.set_ignore("INITIAL", " ")
    engines = [PythonLexerEngine(config), PythonLexerEngine(config)]
    for lexer in engines:
        assert [t.value for t in lexer.tokenize("warm")] == ["warm"]
    config.set_ignore("INITIAL", " \n")
    config.add_rule("INITIAL", "SPECIAL", "ab", priority=10)
    for lexer in engines:
        assert [(t.type, t.value, t.lineno, t.lexpos) for t in lexer.tokenize("\nab c")] == [
            ("SPECIAL", "ab", 2, 1), ("WORD", "c", 2, 4),
        ]


def test_ignored_callback_reentry_refreshes_ignore_and_restores_scan_state():
    lexer = LexerEngine()
    lexer.add_state("inner", "exclusive")
    lexer.add_rule("inner", "INNER", "x")
    lexer.add_rule("INITIAL", "WORD", "[a-z]+")
    trace = []

    def change(token):
        token.lexer.set_ignore("INITIAL", "#\n")
        token.lexer.begin("inner")
        trace.append((token.lexer.token().type, token.lexer.pos))
        token.lexer.begin("INITIAL")

    lexer.add_rule("INITIAL", "CHANGE", "!", change)
    assert [(t.type, t.value, t.lineno, t.lexpos) for t in lexer.tokenize("a!x#\nb")] == [
        ("WORD", "a", 1, 0), ("WORD", "b", 2, 5),
    ]
    assert trace == [("INNER", 3)]


def test_warm_inclusive_scanner_observes_new_initial_rules_and_state_kind():
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "A", "a")
    lexer.add_state("child", "inclusive")
    lexer.add_rule("child", "B", "b")
    lexer.input("aba")
    lexer.begin("child")
    assert lexer.token().type == "A"
    lexer.add_rule("INITIAL", "PAIR", "ba", priority=10)
    assert lexer.token().type == "PAIR"
    lexer.input("ab")
    lexer.begin("child")
    assert lexer.token().type == "A"
    lexer.add_state("child", "exclusive")
    assert lexer.token().type == "B"
    lexer.input("a")
    lexer.begin("child")
    with pytest.raises(LexError):
        lexer.token()


def test_ignored_string_subclass_keeps_membership_calls_and_release_timing():
    lexer = PythonLexerEngine()
    lexer.add_rule("INITIAL", "WORD", "[a-z]+")
    trace = []

    class Ignore(str):
        def __contains__(self, item):
            trace.append(item)
            return super().__contains__(item)

        def __del__(self):
            trace.append("released")

    lexer.set_ignore("INITIAL", Ignore(" "))
    assert [t.value for t in lexer.tokenize("a b")] == ["a", "b"]
    assert trace == ["a", " ", "b"]
    lexer.set_ignore("INITIAL", "")
    assert trace == ["a", " ", "b", "released"]


def test_string_index_reentry_does_not_replace_outer_state_matchers():
    lexer = PythonLexerEngine()
    lexer.add_state("inner", "exclusive")
    lexer.add_rule("INITIAL", "OUTER", "a")
    lexer.add_rule("inner", "INNER", "b")
    trace = []

    class Source(str):
        entered = False

        def __getitem__(self, item):
            if item == 0 and not self.entered:
                self.entered = True
                lexer.begin("inner")
                lexer.pos = 1
                trace.append(lexer.token().type)
                lexer.begin("INITIAL")
            return super().__getitem__(item)

    lexer.input(Source("ab"))
    outer = lexer.token()
    assert (outer.type, outer.value, outer.lexpos) == ("OUTER", "a", 0)
    assert trace == ["INNER"]
    lexer.begin("inner")
    assert lexer.token().type == "INNER"


def test_reduction_symbol_factory_keeps_call_shape_and_metadata_read_order(monkeypatch):
    import rply.backends.python.parser as parser_module

    original = parser_module.ParsedSymbol
    shifted = []
    calls = []

    def factory(*args, **kwargs):
        calls.append((args, kwargs))
        symbol = original(*args, **kwargs)
        if args[0] == "N":
            shifted.append(symbol)
        elif args[0] == "s":
            # Metadata must be read after construction: a custom constructor
            # can inspect and update symbols made earlier in the same parse.
            assert args == ("s",) and kwargs == {}
            shifted[0].value = 9
            shifted[0].lineno = 3
            shifted[0].endlineno = 4
            shifted[0].lexpos = 5
            shifted[0].endlexpos = 6
        return symbol

    saved = []
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], lambda p: saved.append(p))
    grammar.set_start("s")
    monkeypatch.setattr(parser_module, "ParsedSymbol", factory)
    parser = PythonLRParser(ParseTableData.from_table(LRTable(grammar)))
    assert parser.parse([Token("N", 7)]) == 9
    assert saved[0].linespan(0) == (3, 4)
    assert saved[0].lexspan(0) == (5, 6)
    assert sum(args[0] == "s" for args, kwargs in calls) == 1
