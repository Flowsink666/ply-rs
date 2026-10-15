"""SLY compatibility tests."""

from ply_rs import Token
from ply_rs.compat import sly
from tests.fixtures.sly_calculator import build_calculator

SlyCalcLexer, SlyCalcParser = build_calculator(sly)


def test_sly_compat_calculator():
    lexer = SlyCalcLexer()
    parser = SlyCalcParser()

    assert parser.parse(lexer.tokenize("10 + 20 * 3")) == 70


def test_sly_action_view_uses_rhs_positions():
    class PositionLexer(sly.Lexer):
        tokens = {NUMBER}
        ignore = " \n"

        @_(r"\d+")
        def NUMBER(self, tok):
            tok.value = int(tok.value)
            return tok

    class PositionParser(sly.Parser):
        tokens = PositionLexer.tokens

        @_("NUMBER")
        def expr(self, p):
            return p[0], len(p), p.NUMBER, p.lineno, p.index

    assert PositionParser().parse(PositionLexer().tokenize("\n  42")) == (42, 1, 42, 2, 3)


def test_sly_keyword_remapping_is_local_to_each_lexer_class():
    class Words(sly.Lexer):
        tokens = {ID, IF}
        ignore = " "
        ID = r"[a-z]+"
        ID["if"] = IF

    class OtherWords(sly.Lexer):
        tokens = {ID}
        ID = r"[a-z]+"

    assert [(t.type, t.value) for t in Words().tokenize("if word")] == [
        ("IF", "if"), ("ID", "word"),
    ]
    assert next(OtherWords().tokenize("if")).type == "ID"


def test_sly_keyword_remapping_selects_action_before_value_conversion():
    calls = []

    class Words(sly.Lexer):
        tokens = {ID, IF}
        ignore = " "
        ID = r"[a-z]+"
        ID["if"] = IF

        def ID(self, tok):
            calls.append(tok.type)
            tok.value = tok.value.upper()
            return tok

        def IF(self, tok):
            calls.append(tok.type)
            tok.value = "keyword"
            return tok

    assert [(t.type, t.value) for t in Words().tokenize("if word")] == [
        ("IF", "keyword"), ("ID", "WORD"),
    ]
    assert calls == ["IF", "ID"]


def test_sly_ignore_patterns_do_not_emit_tokens():
    class Words(sly.Lexer):
        tokens = {ID}
        ID = r"[a-z]+"
        ignore = " "
        ignore_comment = r"\#[^\n]*"
        ignore_newline = r"\n+"

    assert [(t.type, t.value) for t in Words().tokenize("a#comment\nb")] == [
        ("ID", "a"), ("ID", "b"),
    ]
    assert Words.tokens == {"ID"}


def test_sly_ignore_action_runs_and_discards_returned_token():
    calls = []

    class Words(sly.Lexer):
        tokens = {ID}
        ID = r"[a-z]+"

        @_(r"\n+")
        def ignore_newline(self, tok):
            calls.append(tok.value)
            self.lineno += tok.value.count("\n")
            return tok

    assert [(t.type, t.value, t.lineno) for t in Words().tokenize("a\n\nb")] == [
        ("ID", "a", 1), ("ID", "b", 3),
    ]
    assert calls == ["\n\n"]
    assert Words.tokens == {"ID"}


def test_sly_lexer_inherits_patterns_remaps_and_ignored_rules():
    class Base(sly.Lexer):
        tokens = {ID, IF}
        ignore = " "
        ID = r"[a-z]+"
        ID["if"] = IF
        ignore_comment = r"\#[^\n]*"

    class Child(Base):
        tokens = Base.tokens
        ID["else"] = IF

        def ID(self, tok):
            tok.value = tok.value.upper()
            return tok

        def helper(self):
            "A helper's docstring is not a token pattern."

    assert [(t.type, t.value) for t in Child().tokenize("if else name #hidden")] == [
        ("IF", "if"), ("IF", "else"), ("ID", "NAME"),
    ]
    assert [(t.type, t.value) for t in Base().tokenize("else")] == [("ID", "else")]
    assert Child.tokens == {"ID", "IF"}


def test_sly_lexer_diamond_overrides_follow_mro_and_preserve_order():
    class Base(sly.Lexer):
        tokens = {KEYWORD, ID}
        KEYWORD = "if"
        ID = "[a-z]+"

    class Left(Base):
        tokens = Base.tokens

    class Right(Base):
        tokens = Base.tokens
        KEYWORD = "else"

    class Combined(Left, Right):
        tokens = Base.tokens

    assert [(t.type, t.value) for t in Combined().tokenize("else")] == [("KEYWORD", "else")]
    assert [name for name, _, _ in Combined._sly_rules] == ["KEYWORD", "ID"]


def test_sly_remapped_action_preserves_returned_token_positions():
    class Words(sly.Lexer):
        tokens = {ID, IF}
        ID = "[a-z]+"
        ID["if"] = IF

        def IF(self, tok):
            tok.index = 20
            tok.end = 30
            return tok

    tok = next(Words().tokenize("if"))
    assert (tok.type, tok.index, tok.end) == ("IF", 20, 30)


def test_sly_replacement_tokens_and_eof_follow_recovery_protocol():
    class Parser(sly.Parser):
        tokens = {"N", "BAD"}

        @_("N")
        def s(self, p):
            return p[0]

        def error(self, tok):
            self.errors.append(None if tok is None else tok.type)
            if tok is not None:
                self.errok()
                return next(self.tokens, None)

    parser = Parser()
    parser.errors = []
    assert parser.parse([Token("BAD", "?"), Token("N", 7)]) == 7
    assert parser.parse([]) is None
    assert parser.errors == ["BAD", None]
    assert parser.tokens == {"N", "BAD"}


def test_sly_mro_overrides_rules_without_duplicate_diamond_productions():
    class Base(sly.Parser):
        tokens = {"N"}

        @_("N")
        def s(self, p):
            return "base"

    class Left(Base):
        pass

    class Right(Base):
        @_("N")
        def s(self, p):
            return p[0] + 1

    class Child(Left, Right):
        pass

    parser = Child()
    assert parser.parse([Token("N", 7)]) == 8
    assert parser._table.rr_conflicts == []
    assert len(parser._grammar.Productions) == 2


def test_sly_explicit_bnf_separator_keeps_inherited_productions():
    class Base(sly.Parser):
        tokens = {"N"}

        @_("s ::= N")
        def s(self, p):
            return p[0]

    class Child(Base):
        pass

    assert Child().parse([Token("N", 7)]) == 7
