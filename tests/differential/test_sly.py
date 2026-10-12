"""Compare token streams and parser results against reference SLY."""

import pytest

from rply.compat import sly
from tests.fixtures.sly_calculator import build_calculator

pytestmark = pytest.mark.differential


@pytest.mark.parametrize("expression", [
    "1 + 2 * 3", "(1 + 2) * 3", "100 - 50 - 20 - 5",
    "10 * 20 + 30 * 40", "((2 + 3) * (4 + 5)) / 9",
])
def test_sly_matches_upstream(upstream_sly, expression):
    OurLexer, OurParser = build_calculator(sly)
    ReferenceLexer, ReferenceParser = build_calculator(upstream_sly)
    ours, reference = OurLexer(), ReferenceLexer()
    our_tokens = [(t.type, t.value, t.lineno, t.index) for t in ours.tokenize(expression)]
    reference_tokens = [(t.type, t.value, t.lineno, t.index) for t in reference.tokenize(expression)]
    assert our_tokens == reference_tokens
    assert OurParser().parse(ours.tokenize(expression)) == ReferenceParser().parse(reference.tokenize(expression))


def test_keyword_remapping_and_ignore_actions_match_upstream(upstream_sly):
    def scan(api):
        class Words(api.Lexer):
            tokens = {ID, IF}
            ignore = " "
            ID = r"[a-z]+"
            ID["if"] = IF
            ignore_comment = r"\#[^\n]*"

            def ID(self, tok):
                tok.value = tok.value.upper()
                return tok

            @_(r"\n+")
            def ignore_newline(self, tok):
                self.lineno += tok.value.count("\n")
                return tok

        return [(t.type, t.value, t.lineno, t.index, t.end) for t in Words().tokenize("if word#comment\nother")]

    expected = [("IF", "if", 1, 0, 2), ("ID", "WORD", 1, 3, 7), ("ID", "OTHER", 2, 16, 21)]
    assert scan(sly) == scan(upstream_sly) == expected


def test_inherited_lexer_rules_and_remaps_match_upstream(upstream_sly):
    def scan(api):
        class Base(api.Lexer):
            tokens = {ID, IF}
            ignore = " "
            ID = "[a-z]+"
            ID["if"] = IF
            ignore_comment = r"\#[^\n]*"

        class Child(Base):
            tokens = Base.tokens
            ID["else"] = IF

        return [(t.type, t.value) for t in Child().tokenize("if else word #ignored")]

    assert scan(sly) == scan(upstream_sly) == [("IF", "if"), ("IF", "else"), ("ID", "word")]
