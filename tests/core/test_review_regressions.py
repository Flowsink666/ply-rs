"""Backend parity for contextual regexes, conflicts, and source metadata."""

from types import SimpleNamespace

import pytest

from rply import Grammar, LRTable, LRParser, LexerEngine, Token, LexError


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


def test_lexer_priority_is_stable_across_inclusive_states():
    lexer = LexerEngine()
    lexer.add_state("other", "inclusive")
    lexer.add_rule("INITIAL", "SHORT", "a")
    lexer.add_rule("INITIAL", "FIRST", "ab", priority=10)
    lexer.add_rule("INITIAL", "SECOND", "ab", priority=10)
    lexer.add_rule("other", "LOCAL", "ab", priority=20)
    lexer.input("ab")
    assert lexer.token().type == "FIRST"
    lexer.input("ab")
    lexer.begin("other")
    assert lexer.token().type == "LOCAL"
    # Registering a higher priority rule invalidates active rule caches.
    lexer.add_rule("INITIAL", "NEW", "ab", priority=30)
    lexer.input("ab")
    lexer.begin("other")
    assert lexer.token().type == "NEW"


@pytest.mark.parametrize("reverse", [False, True])
def test_nonassoc_remains_forbidden_after_repeated_shift_entries(reverse):
    grammar = Grammar({"N", "LT", "PLUS"})
    grammar.set_precedence([("nonassoc", ["LT"]), ("left", ["PLUS"])])
    rhs_rules = [["e", "LT", "e"], ["e", "LT", "e", "PLUS", "e"]]
    for rhs in reversed(rhs_rules) if reverse else rhs_rules:
        grammar.add_production("e", rhs)
    grammar.add_production("e", ["N"])
    grammar.set_start("e")
    parser = LRParser(LRTable(grammar))
    assert parser.parse([Token("N", 1), Token("LT", "<"), Token("N", 2)]) == 1
    with pytest.raises(SyntaxError):
        parser.parse([
            Token("N", 1), Token("LT", "<"), Token("N", 2),
            Token("LT", "<"), Token("N", 3),
        ])


def test_rightmost_terminal_with_no_precedence_ends_search():
    grammar = Grammar({"A", "B"})
    grammar.set_precedence([("left", ["A"])])
    grammar.add_production("s", ["A", "B", "tail"])
    grammar.add_production("tail", [])
    grammar.set_start("s")
    assert grammar.Productions[1].prec == ("right", 0)
    assert LRParser(LRTable(grammar)).parse([Token("A", 1), Token("B", 2)]) == 1


def test_lhs_position_edits_propagate_to_parent_rule():
    grammar = Grammar({"N"})
    grammar.add_production("s", ["value"], lambda p: (p.linespan(1), p.lexspan(1)))

    def value(p):
        p.set_lineno(0, 99)
        p.set_lexpos(0, 123)
        p.slice[0].endlineno = 100
        p.slice[0].endlexpos = 130
        return p[1]

    grammar.add_production("value", ["N"], value)
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    assert parser.parse([Token("N", "abc", lineno=2, lexpos=5)]) == ((99, 100), (123, 130))


@pytest.mark.parametrize("value", ["abc", "中文🙂", 123])
def test_generic_tokens_without_end_have_consistent_spans(value):
    grammar = Grammar({"N"})
    grammar.add_production("s", ["N"], lambda p: p.lexspan(1))
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    tok = SimpleNamespace(type="N", value=value, lineno=2, lexpos=5)
    assert parser.parse([tok]) == (5, 5 + len(str(value)))


def test_recovery_token_preserves_error_source_span():
    grammar = Grammar({"BAD", "SEMI"})
    grammar.add_production("s", ["error", "SEMI"], lambda p: (p.linespan(1), p.lexspan(1)))
    grammar.set_start("s")
    parser = LRParser(LRTable(grammar))
    assert parser.parse([
        Token("BAD", "?", lineno=7, lexpos=20),
        Token("SEMI", ";", lineno=7, lexpos=21),
    ]) == ((7, 7), (20, 21))
