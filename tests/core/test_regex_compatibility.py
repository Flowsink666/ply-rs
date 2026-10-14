"""Rust scanning retains CPython regex semantics without backend switching."""

import re
import warnings

import pytest

from rply import LexerEngine, LexError
from rply.backends import native_core


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
    from rply.core import regex_support

    def unavailable(*args, **kwargs):
        raise RecursionError("optional rendering limit")

    monkeypatch.setattr(regex_support, "_render", unavailable)
    lexer = LexerEngine()
    lexer.add_rule("INITIAL", "MATCH", "word")
    assert next(lexer.tokenize("word")).value == "word"


@pytest.mark.parametrize("direct", [False, True])
def test_rejected_rule_preserves_state_and_original_exception(direct):
    lexer = native_core.LexerEngine() if direct else LexerEngine()
    lexer.add_rule("INITIAL", "A", "a")
    lexer.input("a")
    with pytest.raises(re.error):
        lexer.add_rule("missing", "BAD", "(")
    with pytest.raises(ValueError, match="Undefined lexer state"):
        lexer.begin("missing")
    assert lexer.token().value == "a"


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


def test_python_matcher_references_participate_in_native_gc(monkeypatch):
    import gc
    import weakref

    class Owner:
        pass

    owner = Owner()
    owner.engine = native_core.LexerEngine()
    reference = weakref.ref(owner)
    original = re.compile

    class Pattern:
        def __init__(self, compiled, owner):
            self.compiled = compiled
            self.owner = owner

        def match(self, text, pos):
            return self.compiled.match(text, pos)

    monkeypatch.setattr(re, "compile", lambda pattern, flags=0:
                        Pattern(original(pattern, flags), reference()))
    owner.engine.add_rule("INITIAL", "PAIR", r"(a)\1")
    owner.engine.input("aa")
    assert owner.engine.token().value == "aa"
    del owner
    gc.collect()
    assert reference() is None


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


def test_direct_native_token_counts_surrogate_values():
    assert native_core.Token("CHAR", "\ud800a").end == 2


def test_native_token_length_uses_string_content_without_subclass_hooks():
    class Text(str):
        def __len__(self):
            raise AssertionError("token construction called subclass __len__")

    assert native_core.Token("CHAR", Text("\ud800a")).end == 2


def test_direct_native_counters_reject_overflow_without_state_changes():
    from rply.core.lexer_config import MAX_LEXER_COUNTER

    lexer = native_core.LexerEngine()
    lexer.pos = MAX_LEXER_COUNTER
    with pytest.raises(OverflowError):
        lexer.skip()
    assert lexer.pos == MAX_LEXER_COUNTER
    with pytest.raises(OverflowError):
        native_core.Token("CHAR", "a", lexpos=MAX_LEXER_COUNTER)
    assert native_core.Token("CHAR", "a", lexpos=MAX_LEXER_COUNTER, end=0).end == 0


@pytest.mark.parametrize("direct", [False, True])
def test_newline_counter_overflow_preserves_scan_position(direct):
    from rply.core.lexer_config import MAX_LEXER_COUNTER

    lexer = native_core.LexerEngine() if direct else LexerEngine()
    lexer.set_ignore("INITIAL", "\n")
    lexer.input("\n")
    lexer.lineno = MAX_LEXER_COUNTER
    with pytest.raises(OverflowError):
        lexer.token()
    assert lexer.pos == 0
    assert lexer.lineno == MAX_LEXER_COUNTER


@pytest.mark.parametrize("direct", [False, True])
def test_invalid_push_preserves_state_stack(direct):
    lexer = native_core.LexerEngine() if direct else LexerEngine()
    with pytest.raises(ValueError, match="Undefined lexer state"):
        lexer.push_state("missing")
    assert lexer.current_state == "INITIAL"
    assert lexer.state_stack == []


def test_direct_native_literal_configuration_is_atomic_and_reentrant():
    lexer = native_core.LexerEngine()
    lexer.set_literals("x")

    class Literals:
        def __iter__(self):
            lexer.input("x")
            assert lexer.token().value == "x"
            return iter(["+"])

    lexer.set_literals(Literals())
    with pytest.raises(TypeError):
        lexer.set_literals(["-", 1])
    lexer.input("+")
    assert lexer.token().value == "+"


@pytest.mark.parametrize("field", ["callback_owner", "error_func"])
def test_direct_native_configuration_releases_references_after_borrows(field):
    lexer = native_core.LexerEngine()
    observations = []

    class Callback:
        def __call__(self, tok):
            pass

        def __del__(self):
            observations.append(lexer.matcher_kinds)
            observations.append(lexer.callback_owner)

    if field == "callback_owner":
        lexer.callback_owner = Callback()
        lexer.callback_owner = "new owner"
    else:
        lexer.set_error_func("INITIAL", Callback())
        lexer.set_error_func("INITIAL", None)
    assert observations == [{"INITIAL": []}, "new owner" if field == "callback_owner" else None]
