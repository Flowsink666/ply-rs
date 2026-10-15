"""Grouped scanning preserves rule semantics, order, flags, and warnings."""

import re
import warnings

import pytest

from ply_rs import LexError, LexerEngine


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
    lexer = LexerEngine()
    lexer.set_ignore("INITIAL", " ")
    for name, pattern in zip(("A", "B"), patterns):
        lexer.add_rule("INITIAL", name, pattern)
    assert [(token.type, token.value) for token in lexer.tokenize(text)] == expected


def test_grouped_scanning_keeps_rule_flags_local():
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "ASCII", r"\w+", flags=re.ASCII)
    lexer.add_rule("INITIAL", "UNICODE", r"\w+")
    lexer.set_ignore("INITIAL", " ")
    assert [(token.type, token.value) for token in lexer.tokenize("ascii 中文")] == [
        ("ASCII", "ascii"), ("UNICODE", "中文"),
    ]


def test_global_inline_flags_do_not_leak_into_neighbor_rules():
    lexer = LexerEngine()
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
        lexer = LexerEngine()
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
    lexer = LexerEngine()
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
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "A", "a # trailing comment", flags=re.VERBOSE)
    lexer.add_rule("INITIAL", "B", "b", flags=re.VERBOSE)
    assert [(token.type, token.value) for token in lexer.tokenize("ab")] == [("A", "a"), ("B", "b")]


def test_python_re_rules_keep_their_order_between_native_groups():
    lexer = LexerEngine()
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


@pytest.mark.parametrize("text", ["\ud800", "\ud800a", "a\udfff", "\ud800\udc00"])
def test_surrogates_preserve_character_counts_and_scan_to_eof(text):
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "CHAR", ".")
    lexer.input(text)
    assert lexer.length == len(text)
    assert [(t.value, t.lexpos, t.end) for t in lexer.tokenize()] == [
        (character, index, index + 1) for index, character in enumerate(text)]
    assert lexer.token() is None
    snapshot = lexer.capture_state()
    lexer.input("ordinary")
    lexer.restore_state(snapshot)
    assert lexer.data == text
    assert lexer.length == len(text)
    assert lexer.token() is None


@pytest.mark.parametrize("setup", ["ignore", "literal"])
def test_surrogate_is_not_a_replacement_character(setup):
    lexer = LexerEngine()
    if setup == "ignore":
        lexer.set_ignore("INITIAL", "\ufffd")
    else:
        lexer.set_literals("\ufffd")
    with pytest.raises(LexError) as error:
        next(lexer.tokenize("\ud800"))
    assert error.value.text == "\ud800"
    assert error.value.error_index == 0


def test_surrogate_ignore_and_literal_characters_keep_original_values():
    lexer = LexerEngine()
    lexer.set_ignore("INITIAL", "\ud800")
    lexer.set_literals("\udfff")
    lexer.add_rule("INITIAL", "A", "a")
    assert [(t.type, t.value, t.lexpos, t.end) for t in lexer.tokenize("\ud800\udfffa")] == [
        ("\udfff", "\udfff", 1, 2), ("A", "a", 2, 3)]
