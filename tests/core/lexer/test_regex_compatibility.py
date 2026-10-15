"""Regex compatibility contracts."""

import re
import warnings

import pytest

from ply_rs import LexError, LexerEngine


@pytest.mark.parametrize("pattern,flags,text,pos", [
    (r"\w+", 0, "a\u0301", 0),
    (r"\s", 0, "\x1c", 0),
    (r"a$", 0, "a\n", 0),
    (r"[a-z]", re.I, "İ", 0),
    (r"[a&&b]", 0, "&", 0),
    (r"a(?=b)", 0, "ab", 0),
    (r"(?<=a)b", 0, "ab", 1),
    (r"(?<!b)a", 0, "ba", 1),
    (r"(a)\1", 0, "aa", 0),
    (r"(?P<x>a)(?P=x)", 0, "aa", 0),
    (r"(a)?(?(1)b|c)", 0, "ab", 0),
    (r"(a)?(?(1)b|c)", 0, "c", 0),
    (r"a\Z", 0, "a", 0),
    (r"(?i)alpha", 0, "ALPHA", 0),
    (r"(?a:\w+)(?u:\w+)", 0, "abc中文", 0),
    (r"\w+", re.A, "abc中文", 0),
    (r".", re.A, "中", 0),
    (r"[^a]+", re.A, "中文", 0),
    (r"\W+", re.A, "中文", 0),
    (r"\bword\b", re.A, "éwordé", 1),
    (r"^a", 0, "ba", 1),
    (r"^a", re.M, "b\na", 2),
    (r"a.*?b", re.S, "a\nb", 0),
    ("a # trailing comment", re.X, "a", 0),
    (r"a|ab", 0, "ab", 0),
    (r"\N{SNOWMAN}", 0, "☃", 0),
    ("\ud800", 0, "\ud800", 0),
    (".", 0, "\ud800", 0),
    ("abc", re.IGNORECASE, "ABC", 0),
    ("abc$", re.MULTILINE, "abc\n", 0),
    ("a.b", re.DOTALL, "a\nb", 0),
    ("a b c # comment", re.VERBOSE, "abc", 0),
    (r"\w+", re.UNICODE, "中文", 0),
    ("a . b # comment", re.IGNORECASE | re.DOTALL | re.VERBOSE, "A\nB", 0),
])
def test_rules_agree_with_python_re(pattern, flags, text, pos):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        expected = re.compile(pattern, flags).match(text, pos)
        lexer = LexerEngine()
        lexer.add_rule("INITIAL", "MATCH", pattern, flags=flags)
    lexer.input(text)
    lexer.pos = pos
    assert lexer.backend == "rust"
    if expected is None:
        with pytest.raises(LexError):
            lexer.token()
    else:
        token = lexer.token()
        assert (token.value, token.lexpos, token.end) == (expected.group(), pos, expected.end())


def test_ascii_classes_and_unicode_dot_both_use_native_matching():
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "WORD", r"\w+", flags=re.A)
    lexer.add_rule("INITIAL", "CHAR", ".", flags=re.A)
    assert lexer._runtime.engine.matcher_kinds["INITIAL"] == ["native", "native"]
    assert [(t.type, t.value) for t in lexer.tokenize("abc中")] == [("WORD", "abc"), ("CHAR", "中")]


def test_ascii_input_fast_path_keeps_unicode_semantics_after_input_changes():
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "KEYWORD", r"word\b")
    lexer.add_rule("INITIAL", "WORD", r"\w+")
    assert [t.type for t in lexer.tokenize("word")] == ["KEYWORD"]
    # A non-ASCII word character prevents that keyword boundary.
    assert [(t.type, t.value) for t in lexer.tokenize("wordé")] == [("WORD", "wordé")]
    assert [t.type for t in lexer.tokenize("word")] == ["KEYWORD"]


def test_unicode_whitespace_ascii_fast_path_includes_python_control_separators():
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "SPACE", r"\s+")
    assert [t.value for t in lexer.tokenize("\x1c\x1d\x1e\x1f")] == ["\x1c\x1d\x1e\x1f"]


def test_optional_regex_translation_failure_uses_compiled_matcher(monkeypatch):
    try:
        from re import _parser as parser
    except ImportError:
        import sre_parse as parser

    re.compile("already-cached-pattern")
    def unavailable(*args, **kwargs):
        raise RecursionError("optional translation limit")
    monkeypatch.setattr(parser, "parse", unavailable)
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "MATCH", "already-cached-pattern")
    assert lexer._runtime.engine.matcher_kinds["INITIAL"] == ["python-re"]
    assert next(lexer.tokenize("already-cached-pattern")).value == "already-cached-pattern"


def test_optional_regex_rendering_limit_uses_compiled_matcher(monkeypatch):
    from ply_rs.core import regex_support

    def unavailable(*args, **kwargs):
        raise RecursionError("optional rendering limit")

    monkeypatch.setattr(regex_support, "_render", unavailable)
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "MATCH", "word")
    assert next(lexer.tokenize("word")).value == "word"


def test_mixed_matchers_keep_rule_order_and_global_flags_local():
    lexer = LexerEngine()
    for name, pattern, flags in [("A", "a", 0), ("PAIR", r"(b)\1", 0),
                                 ("B", "b+", 0), ("UPPER", "c", re.I), ("D", "d", 0)]:
        lexer.add_rule("INITIAL", name, pattern, flags=flags)
    lexer.input("abbCD")
    assert [(t.type, t.value) for t in (lexer.token(), lexer.token(), lexer.token())] == [
        ("A", "a"), ("PAIR", "bb"), ("UPPER", "C")]
    with pytest.raises(LexError):
        lexer.token()


def test_multiline_flag_does_not_skip_unmatched_prefix():
    engine = LexerEngine()
    engine.add_rule("INITIAL", "WORD", "abc", flags=re.MULTILINE)
    engine.input("?\nabc")
    with pytest.raises(LexError):
        engine.token()
    assert engine.pos == 0


@pytest.mark.parametrize("pattern,text,accepted", [
    (r"^a", "ba", False),
    (r"\Aa", "ba", False),
    (r"\ba", "ba", False),
    (r"\ba", "中a", False),
    (r"\Ba", "ba", True),
    (r"(?m)^a", "b\na", True),
    (r"(?m)^a", "ba", False),
])
def test_lexer_assertions_retain_preceding_context(pattern, text, accepted):
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "PREFIX", r"b|中")
    lexer.add_rule("INITIAL", "A", pattern)
    lexer.set_ignore("INITIAL", "\n")
    lexer.input(text)
    assert lexer.token().type == "PREFIX"
    if accepted:
        assert lexer.token().value == "a"
        assert lexer.token() is None
    else:
        with pytest.raises(LexError):
            lexer.token()
